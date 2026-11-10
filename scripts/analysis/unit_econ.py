#!/usr/bin/env python3
"""Юнит-экономика подписки/сервиса: ARPU, отток, LTV, CAC, окупаемость + чувствительность ±20 %.

Из параметров:
  python3 scripts/analysis/unit_econ.py --arpu 350 --churn 6% --cac 1200 --margin 0.6 --out projects/<slug>/data/unit_econ.md
Из CSV по периодам (столбцы period, revenue, active_users, new_users, churned_users, marketing_spend;
переименование: --map revenue=выручка,active_users=активные):
  python3 scripts/analysis/unit_econ.py --csv monthly.csv --last 3 --margin 0.6 --out projects/<slug>/data/unit_econ.md

Модель (все показатели — за один период, по умолчанию месяц):
  маржа на пользователя m = ARPU × margin; удержание за период r = (1 − churn) / (1 + discount)
  LTV = m × (1 − r^H) / (1 − r)  (H = --horizon; без горизонта: m / (1 − r); при discount = 0 → m / churn)
  окупаемость простая = CAC / m; с учётом оттока — первый период, когда накопленная маржа ≥ CAC.
Явные допущения печатаются в отчёте.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

CSV_COLS = ["period", "revenue", "active_users", "new_users", "churned_users", "marketing_spend"]
PARAMS = ["arpu", "churn", "cac", "margin"]
NAMES = {"arpu": "ARPU", "churn": "Отток за период", "cac": "CAC", "margin": "Валовая маржа"}
MAX_PERIODS = 600


def ltv(arpu, churn, margin, discount=0.0, horizon=None) -> float:
    m = arpu * margin
    r = (1 - churn) / (1 + discount)
    if horizon:
        return m * (1 - r ** horizon) / (1 - r) if r != 1 else m * horizon
    if r >= 1:
        return float("inf")
    return m / (1 - r)


def payback(arpu, churn, cac, margin, discount=0.0) -> tuple[float | None, int | None]:
    m = arpu * margin
    simple = cac / m if m > 0 else None
    if cac <= 0:
        return simple, 0 if cac == 0 else None
    cum, s = 0.0, 1.0
    r = (1 - churn) / (1 + discount)
    for t in range(1, MAX_PERIODS + 1):
        cum += m * s
        if cum >= cac:
            return simple, t
        s *= r
    return simple, None


def metrics(p: dict, discount=0.0, horizon=None) -> dict:
    l = ltv(p["arpu"], p["churn"], p["margin"], discount, horizon)
    simple, adj = payback(p["arpu"], p["churn"], p["cac"], p["margin"], discount)
    return {"ltv": l, "ltv_cac": l / p["cac"] if p["cac"] > 0 else None, "payback_simple": simple, "payback_adj": adj,
            "lifetime": 1 / p["churn"] if p["churn"] else None}


def from_csv(path, last=3, colmap=None) -> tuple[dict, dict, int, list[str]]:
    header, rows, _ = c.read_csv(path)
    colmap = colmap or {}
    cols = {k: colmap.get(k, k) for k in CSV_COLS}
    c.require_columns(header, list(cols.values()), path)
    notes = []
    used = rows[-last:] if last else rows
    if len(used) < last:
        notes.append(f"в файле только {len(used)} периодов, а запрошено {last}")

    def total(key):
        vals = [c.parse_number(r[cols[key]]) for r in used]
        miss = sum(1 for v in vals if v is None)
        if miss:
            notes.append(f"`{cols[key]}`: {miss} пропусков в последних {len(used)} периодах — исключены из суммы")
        return sum(v for v in vals if v is not None)

    rev, act, new, churned, mkt = (total(k) for k in CSV_COLS[1:])
    if not act or not new:
        raise SystemExit("Ошибка: нулевая сумма active_users или new_users — нельзя посчитать ARPU/CAC")
    periods = [r[cols["period"]] for r in used]
    vals = {"arpu": rev / act, "churn": churned / act, "cac": mkt / new}
    origin = {k: f"из CSV: среднее за периоды {periods[0]}…{periods[-1]} ({len(used)})" for k in vals}
    notes.append("ARPU = Σ выручки / Σ активных; отток = Σ ушедших / Σ активных; CAC = Σ маркетинга / Σ новых "
                 "(весь маркетинг отнесён на новых — завышает CAC, если есть затраты на удержание)")
    return vals, origin, len(rows), notes


def sensitivity(base: dict, discount, horizon, delta=0.2) -> list[dict]:
    out = []
    for k in PARAMS:
        for f in (1 - delta, 1 + delta):
            p = dict(base)
            p[k] = base[k] * f
            if k in ("churn", "margin") and p[k] > 1:
                p[k] = 1.0
            m = metrics(p, discount, horizon)
            out.append({"param": k, "factor": f, "value": p[k], **m})
    return out


def render(base, origin, res, sens, assumptions, notes, unit, period, prov) -> tuple[str, str]:
    L = ["# Юнит-экономика", "", f"Единицы: деньги — {unit}; период — {period}", "",
         "## Входные параметры", "", "| Параметр | Значение | Откуда |", "|---|---|---|"]
    for k in PARAMS:
        v = c.pct(base[k]) if k in ("churn", "margin") else f"{c.fmt(base[k])} {unit}"
        L.append(f"| {NAMES[k]} | {v} | {origin[k]} |")
    L += ["", "## Результат", "", "| Показатель | Значение |", "|---|---|",
          f"| LTV (маржинальный) | {c.fmt(res['ltv'])} {unit} |",
          f"| LTV / CAC | {c.fmt(res['ltv_cac'])} |",
          f"| Средний срок жизни (1 / отток) | {c.fmt(res['lifetime'], 1)} {period} |",
          f"| Окупаемость CAC, простая (CAC / маржа за период) | {c.fmt(res['payback_simple'], 1)} {period} |",
          f"| Окупаемость с учётом оттока | {res['payback_adj'] if res['payback_adj'] is not None else 'не окупается'} {period if res['payback_adj'] is not None else ''} |",
          "", "## Чувствительность (каждый параметр отдельно ±20 %)", "",
          "| Параметр | Изменение | Значение | LTV | LTV/CAC | Окупаемость с оттоком |", "|---|---|---|---|---|---|"]
    swings = {}
    for s in sens:
        v = c.pct(s["value"]) if s["param"] in ("churn", "margin") else c.fmt(s["value"])
        L.append(f"| {NAMES[s['param']]} | {'−' if s['factor'] < 1 else '+'}{abs(s['factor'] - 1):.0%} | {v} | "
                 f"{c.fmt(s['ltv'])} | {c.fmt(s['ltv_cac'])} | {s['payback_adj'] if s['payback_adj'] is not None else 'не окупается'} |")
        if s["ltv_cac"] is not None:
            swings.setdefault(s["param"], []).append(s["ltv_cac"])
    order = sorted(swings, key=lambda k: -(max(swings[k]) - min(swings[k])))
    L += ["", ("Сильнее всего на LTV/CAC влияют: " + ", ".join(
        f"{NAMES[k]} ({c.fmt(min(swings[k]))}–{c.fmt(max(swings[k]))})" for k in order) +
        ". Уточнять в первую очередь — первый параметр списка.") if order else
        "LTV/CAC не определён (CAC = 0): чувствительность по LTV/CAC не считается.",
        "", "## Допущения", ""] + [f"- {a}" for a in assumptions]
    L += ["", "## Замечания к данным", ""] + ([f"- {n}" for n in notes] or ["- нет"])
    ratios = [s["ltv_cac"] for s in sens if s["ltv_cac"] is not None]
    rng = f"{c.fmt(min(ratios))}–{c.fmt(max(ratios))}" if ratios else "не определён"
    lc = c.fmt(res['ltv_cac']) if res['ltv_cac'] is not None else "не определён (CAC = 0)"
    itog = (f"ИТОГ: LTV {c.fmt(res['ltv'])} {unit}, CAC {c.fmt(base['cac'])} {unit}, LTV/CAC {lc} "
            f"(при ±20 % по одному параметру: {rng}), окупаемость с оттоком: "
            f"{str(res['payback_adj']) + ' ' + period if res['payback_adj'] is not None else 'не окупается'}; тип — estimate.")
    L += ["", itog, "", prov]
    return "\n".join(L), itog


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Юнит-экономика: ARPU, отток, LTV, CAC, окупаемость, чувствительность ±20 %.")
    p.add_argument("--csv", help="CSV по периодам (см. описание вверху файла)")
    p.add_argument("--map", help="переименование столбцов CSV: revenue=выручка,active_users=активные")
    p.add_argument("--last", type=int, default=3, help="сколько последних периодов CSV усреднять (по умолчанию 3)")
    p.add_argument("--arpu", type=float, help="выручка на пользователя за период")
    p.add_argument("--churn", type=c.share_type, help="доля ушедших за период: 0.06 или 6%%")
    p.add_argument("--cac", type=float, help="стоимость привлечения одного пользователя")
    p.add_argument("--margin", type=c.share_type, default=None, help="валовая маржа: 0.6 или 60%% (по умолчанию 100 %% — допущение!)")
    p.add_argument("--discount", type=c.share_type, default="0", help="ставка дисконтирования за период (0.01 или 1%%)")
    p.add_argument("--horizon", type=int, help="горизонт LTV в периодах (по умолчанию без ограничения)")
    p.add_argument("--unit", default="руб.", help="денежная единица")
    p.add_argument("--period", default="мес.", help="название периода")
    c.add_output_args(p)
    a = p.parse_args(argv)

    base, origin, notes, rows = {}, {}, [], None
    if a.csv:
        colmap = dict(x.split("=", 1) for x in a.map.split(",")) if a.map else None
        base, origin, rows, notes = from_csv(a.csv, a.last, colmap)
    for k, raw in (("arpu", a.arpu), ("cac", a.cac)):
        if raw is not None:
            base[k], origin[k] = float(raw), "параметр командной строки"
    if a.churn is not None:
        base["churn"], origin["churn"] = a.churn, "параметр командной строки"
    assumptions = ["ARPU, отток и маржа постоянны во времени (на практике отток новых выше — LTV может быть завышен)",
                   "отток и ARPU относятся к одному периоду и одной базе пользователей"]
    if a.margin is None:
        base["margin"], origin["margin"] = 1.0, "**допущение**: маржа не задана, принята 100 %"
        assumptions.append("валовая маржа 100 % — LTV считается по выручке и завышен; задайте --margin")
    else:
        base["margin"], origin["margin"] = a.margin, "параметр командной строки"
    missing = [k for k in PARAMS if k not in base]
    if missing:
        p.error(f"не заданы параметры {missing} (через --csv или явно)")
    if not 0 < base["churn"] <= 1:
        p.error("отток должен быть в (0; 1]")
    discount = a.discount
    assumptions.append(f"дисконтирование: {c.pct(discount)} за период; горизонт: "
                       f"{str(a.horizon) + ' периодов' if a.horizon else 'без ограничения'}")

    res = metrics(base, discount, a.horizon)
    sens = sensitivity(base, discount, a.horizon)
    out = c.resolve_out(a, f"unit_econ_{Path(a.csv).stem if a.csv else 'params'}.md")
    prov = c.provenance_md("unit_econ.py", [(a.csv, rows)] if a.csv else [], vars(a))
    report, itog = render(base, origin, res, sens, assumptions, notes, a.unit, a.period, prov)
    if out:
        c.write_csv(out.with_suffix(".csv"), ["param", "factor", "value", "ltv", "ltv_cac", "payback_adj"],
                    [["base", 1.0, "", round(res["ltv"], 2), "" if res["ltv_cac"] is None else round(res["ltv_cac"], 3), "" if res["payback_adj"] is None else res["payback_adj"]]] +
                    [[s["param"], round(s["factor"], 2), round(s["value"], 6), round(s["ltv"], 2),
                      "" if s["ltv_cac"] is None else round(s["ltv_cac"], 3), "" if s["payback_adj"] is None else s["payback_adj"]] for s in sens])
    tail = f" sha256 {c.sha256_file(a.csv)[:12]}" if a.csv else ""
    c.finish(out, report, itog + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
