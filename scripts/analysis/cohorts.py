#!/usr/bin/env python3
"""Когортное удержание.

Режим 1 — события (одна строка = активность пользователя в дату):
  python3 scripts/analysis/cohorts.py --events events.csv --user-col user_id --date-col date \
      --period month --out projects/<slug>/data/cohorts.md
Когорта = период первой активности; удержание на периоде k = доля когорты, активная в период k.

Режим 2 — агрегаты (cohort, period = номер периода от 0, users):
  python3 scripts/analysis/cohorts.py --agg cohorts_agg.csv --out projects/<slug>/data/cohorts.md
Размер когорты = users на периоде 0 (или столбец --size-col).

Ненаблюдаемые ячейки (когорта ещё «не дожила» до периода) остаются пустыми, а не нулями.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402


def period_key(d: dt.date, period: str) -> int:
    if period == "month":
        return d.year * 12 + d.month - 1
    if period == "week":
        return (d - dt.timedelta(days=d.weekday())).toordinal() // 7
    raise ValueError(period)


def period_label(k: int, period: str) -> str:
    if period == "month":
        return f"{k // 12}-{k % 12 + 1:02d}"
    return dt.date.fromordinal(k * 7 + 1).isoformat()  # понедельник недели


def from_events(path, user_col="user_id", date_col="date", period="month") -> dict:
    header, rows, _ = c.read_csv(path)
    c.require_columns(header, [user_col, date_col], path)
    active: dict[str, set[int]] = defaultdict(set)
    bad = 0
    last_date = None
    for r in rows:
        u = r[user_col]
        pd_ = c.parse_date(r[date_col])
        if c.is_missing(u) or not pd_:
            bad += 1
            continue
        active[u].add(period_key(pd_[0], period))
        last_date = pd_[0] if last_date is None or pd_[0] > last_date else last_date
    if not active:
        raise SystemExit("Ошибка: нет ни одной строки с распознанными user и датой")
    max_p = max(max(s) for s in active.values())
    sizes: dict[int, int] = defaultdict(int)
    counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for periods in active.values():
        start = min(periods)
        sizes[start] += 1
        for p in periods:
            counts[start][p - start] += 1
    notes = []
    if bad:
        notes.append(f"пропущено строк без пользователя или с нераспознанной датой: {bad}")
    end_of_period = (last_date + dt.timedelta(days=1))
    if period_key(end_of_period, period) == period_key(last_date, period):
        notes.append(f"последний период ({period_label(max_p, period)}) неполный: данные до {last_date} — "
                     f"его удержание занижено, не сравнивайте с полными")
    return build(sizes, counts, max_p, period, rows=len(rows), notes=notes)


def from_agg(path, cohort_col="cohort", period_col="period", users_col="users", size_col=None) -> dict:
    header, rows, _ = c.read_csv(path)
    c.require_columns(header, [cohort_col, period_col, users_col] + ([size_col] if size_col else []), path)
    labels = sorted({r[cohort_col] for r in rows})
    idx = {lab: i for i, lab in enumerate(labels)}
    sizes: dict[int, int] = {}
    counts: dict[int, dict[int, int]] = defaultdict(dict)
    notes = []
    amb = c.count_ambiguous(r[users_col] for r in rows)
    if amb:
        notes.append(f"{amb} значений `{users_col}` вида «1,234» неоднозначны (1 234 или 1,234?) — "
                     f"читаются как десятичные и округляются вниз; проверьте формат выгрузки")
    for r in rows:
        k, p, u = idx[r[cohort_col]], c.parse_number(r[period_col]), c.parse_number(r[users_col])
        if p is None or u is None:
            notes.append(f"строка пропущена (нет числа): {r}")
            continue
        counts[k][int(p)] = int(u)
        if size_col and c.parse_number(r[size_col]) is not None:
            sizes[k] = int(c.parse_number(r[size_col]))
    for k in counts:
        if k not in sizes:
            if 0 not in counts[k]:
                raise SystemExit(f"Ошибка: у когорты {labels[k]} нет периода 0 и не задан --size-col")
            sizes[k] = counts[k][0]
    # в агрегатах ненаблюдаемое = отсутствующая строка; максимум периода по когорте берём из данных
    res = build(sizes, counts, None, "label", rows=len(rows), notes=notes, labels=labels)
    return res


def build(sizes, counts, max_p, period, rows, notes, labels=None) -> dict:
    max_offset = max(max(v) for v in counts.values() if v) if counts else 0
    matrix = []
    for k in sorted(sizes):
        observed = (max_p - k) if max_p is not None else max(counts[k])
        cells = []
        for off in range(max_offset + 1):
            if off > observed:
                cells.append(None)
            else:
                n = counts[k].get(off, 0)
                cells.append(n / sizes[k] if sizes[k] else None)
                if n > sizes[k]:
                    notes.append(f"когорта {k}: на периоде {off} активных ({n}) больше размера ({sizes[k]}) — ошибка данных")
        label = labels[k] if labels else period_label(k, period)
        matrix.append({"cohort": label, "size": sizes[k], "retention": cells})
    curve = []
    for off in range(max_offset + 1):
        obs = [(m["size"], m["retention"][off]) for m in matrix if m["retention"][off] is not None]
        tot = sum(s for s, _ in obs)
        curve.append({"offset": off, "cohorts": len(obs), "users": tot,
                      "retention": (sum(s * r for s, r in obs) / tot) if tot else None})
    small = [m["cohort"] for m in matrix if m["size"] < 30]
    if small:
        notes.append(f"когорты меньше 30 пользователей (шумные): {small}")
    return {"matrix": matrix, "curve": curve, "rows": rows, "notes": notes, "max_offset": max_offset}


def render(res, period_name, prov) -> tuple[str, str]:
    n = res["max_offset"]
    L = ["# Когортное удержание", "",
         f"Период: {period_name}. Ячейка = доля когорты, активная в период k после первого. Пусто — период ещё не наступил (не ноль).", "",
         "| Когорта | Размер | " + " | ".join(f"k={i}" for i in range(n + 1)) + " |",
         "|---|---|" + "---|" * (n + 1)]
    for m in res["matrix"]:
        L.append(f"| {m['cohort']} | {m['size']} | " + " | ".join("" if r is None else c.pct(r) for r in m["retention"]) + " |")
    L += ["", "## Средняя кривая (взвешенная по размеру когорт, только наблюдаемые ячейки)", "",
          "| k | Когорт | Пользователей в базе | Удержание |", "|---|---|---|---|"]
    for p in res["curve"]:
        L.append(f"| {p['offset']} | {p['cohorts']} | {p['users']} | {c.pct(p['retention'])} |")
    L += ["", "## Замечания к данным", ""] + ([f"- {x}" for x in res["notes"]] or ["- нет"])
    pts = [p for p in res["curve"] if p["offset"] in (1, 2, 3, 6, 12) and p["retention"] is not None]
    itog = "ИТОГ: удержание " + "; ".join(f"k={p['offset']}: {c.pct(p['retention'])} (когорт {p['cohorts']}, польз. {p['users']})" for p in pts) \
        if pts else "ИТОГ: данных для удержания на k≥1 нет"
    itog += f"; когорт всего {len(res['matrix'])}, период — {period_name}."
    L += ["", itog, "", prov]
    return "\n".join(L), itog


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Когортное удержание из событий или агрегатов.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--events", help="CSV событий: пользователь, дата")
    src.add_argument("--agg", help="CSV агрегатов: cohort, period (0,1,2…), users")
    p.add_argument("--user-col", default="user_id")
    p.add_argument("--date-col", default="date")
    p.add_argument("--period", choices=["month", "week"], default="month")
    p.add_argument("--cohort-col", default="cohort")
    p.add_argument("--period-col", default="period")
    p.add_argument("--users-col", default="users")
    p.add_argument("--size-col", help="столбец размера когорты (если нет строки period=0)")
    c.add_output_args(p)
    a = p.parse_args(argv)
    path = a.events or a.agg
    if a.events:
        res = from_events(a.events, a.user_col, a.date_col, a.period)
        period_name = {"month": "месяц", "week": "неделя (с понедельника)"}[a.period]
    else:
        res = from_agg(a.agg, a.cohort_col, a.period_col, a.users_col, a.size_col)
        period_name = "как в исходных агрегатах"
    out = c.resolve_out(a, f"cohorts_{Path(path).stem}.md")
    report, itog = render(res, period_name, c.provenance_md("cohorts.py", [(path, res["rows"])], vars(a)))
    if out:
        header = ["cohort", "size"] + [f"k{i}" for i in range(res["max_offset"] + 1)]
        c.write_csv(out.with_suffix(".csv"), header,
                    [[m["cohort"], m["size"]] + ["" if r is None else round(r, 4) for r in m["retention"]] for m in res["matrix"]])
    c.finish(out, report, f"{itog} sha256 {c.sha256_file(path)[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
