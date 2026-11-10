#!/usr/bin/env python3
"""Разрез метрики «сегмент × период»: где именно сосредоточено изменение.

Зачем: средняя по всем пользователям скрывает причину (парадокс Симпсона: сменился состав трафика,
а не сама метрика; или упал один сегмент). Скрипт делит метрику по сегментам и периодам и раскладывает
общее изменение «до → после» на вклад каждого сегмента.

Режим 1 — журнал событий (пользователь, событие, дата): доля пользователей, у которых было целевое событие,
среди тех, у кого было базовое; дата пользователя = дата первого базового события; сегменты берутся из первой
строки пользователя:
  python3 scripts/analysis/segment.py --csv events.csv --user-col user_id --event-col event \\
      --base-event onboarding_start --target-event onboarding_complete \\
      --date-col event_date --split 2026-09-01 --segments platform,channel
Режим 2 — одна строка = одно наблюдение, метрика — столбец (0/1 → доля, число → среднее):
  python3 scripts/analysis/segment.py --csv orders.csv --value-col paid --date-col date --split 2026-09-01 --segments region

Период: --split ДАТА (до: дата < ДАТА; после: дата ≥ ДАТА) или --by week (метрика по неделям).
Сегменты: --segments a,b — одиночные и (при --depth 2, по умолчанию) парные сочетания столбцов.

Разложение (для каждого разбиения на сегменты): изменение общей метрики = Σ вкладов сегментов, вклад =
эффект ставки w_после·(m_после − m_до) + эффект состава (w_после − w_до)·(m_до − M_до), где w — доля сегмента
в наблюдениях, m — метрика сегмента, M — общая метрика «до». Сегмент без наблюдений «до» учитывается целиком как эффект ставки.
Сосредоточение: сегмент помечается, если его эффект ставки ≥ 30 % общего изменения, наблюдений ≥ --min-n в обоих периодах
и разность значима (|z| ≥ 1,96, нормальное приближение; это ориентир, не доказательство причины).
Ограничения: связь, не причинность; сегменты могут пересекаться по причинам (платформа и версия приложения —
одно и то же) — смотрите контрольные сегменты без изменения; поздние когорты могут иметь неполное окно наблюдения.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

MAX_LEVELS = 12
CONC_SHARE = 0.30
Z = 1.959964


class Acc:
    """Накопитель: n, сумма, сумма квадратов."""
    __slots__ = ("n", "s", "q")

    def __init__(self):
        self.n, self.s, self.q = 0, 0.0, 0.0

    def add(self, v: float):
        self.n += 1
        self.s += v
        self.q += v * v

    @property
    def mean(self):
        return self.s / self.n if self.n else None

    @property
    def var(self):
        if self.n < 2:
            return None
        return max(0.0, (self.q - self.s * self.s / self.n) / (self.n - 1))


def load_observations(a) -> tuple[list[dict], list[str], int, int]:
    """Возвращает (наблюдения {date, value, seg:{col:val}}, заметки, строк, пропущено)."""
    header, rows, meta = c.read_csv(a.csv)
    segs = [s.strip() for s in a.segments.split(",") if s.strip()]
    need = [a.date_col] + segs
    notes = []
    if a.value_col:
        need.append(a.value_col)
    else:
        need += [a.user_col, a.event_col]
    c.require_columns(header, need, a.csv)
    obs, skipped = [], 0
    if a.value_col:
        for r in rows:
            d, v = c.parse_date(r[a.date_col]), c.parse_number(r[a.value_col])
            if d is None or v is None:
                skipped += 1
                continue
            obs.append({"date": d[0], "value": v, "seg": {s: r[s] or "(пусто)" for s in segs}})
        amb = c.count_ambiguous(r[a.value_col] for r in rows)
        if amb:
            notes.append(f"{amb} значений `{a.value_col}` вида «1,234» неоднозначны (тысячи или десятичная дробь)")
    else:
        if not (a.base_event and a.target_event):
            raise SystemExit("Ошибка: для журнала событий нужны --base-event и --target-event (или режим --value-col).")
        first: dict[str, dict] = {}
        did_target: set[str] = set()
        for r in rows:
            e = r[a.event_col]
            u = r[a.user_col]
            d = c.parse_date(r[a.date_col])
            if d is None:
                skipped += 1
                continue
            if e == a.base_event:
                cur = first.get(u)
                if cur is None or d[0] < cur["date"]:
                    first[u] = {"date": d[0], "seg": {s: r[s] or "(пусто)" for s in segs}}
            if e == a.target_event:
                did_target.add(u)
        for u, f in first.items():
            obs.append({"date": f["date"], "value": 1.0 if u in did_target else 0.0, "seg": f["seg"]})
        orphan = len(did_target - set(first))
        if orphan:
            notes.append(f"{orphan} пользователей с целевым событием без базового — не учтены")
    if skipped:
        notes.append(f"пропущено строк без даты/значения: {skipped}")
    return obs, notes, len(rows), skipped


def is_binary(obs) -> bool:
    return all(o["value"] in (0.0, 1.0) for o in obs)


def two_sided_z(b: Acc, a: Acc) -> float | None:
    if b.n < 2 or a.n < 2:
        return None
    se = math.sqrt((b.var or 0) / b.n + (a.var or 0) / a.n)
    if se == 0:
        return None
    return (a.mean - b.mean) / se


def decompose(obs, split: dt.date, dims: tuple[str, ...], min_n: int) -> dict:
    before: dict[tuple, Acc] = defaultdict(Acc)
    after: dict[tuple, Acc] = defaultdict(Acc)
    tb, ta = Acc(), Acc()
    for o in obs:
        key = tuple(o["seg"][d] for d in dims)
        if o["date"] < split:
            before[key].add(o["value"])
            tb.add(o["value"])
        else:
            after[key].add(o["value"])
            ta.add(o["value"])
    keys = sorted(set(before) | set(after))
    M_b = tb.mean
    total = (ta.mean - M_b) if (tb.n and ta.n) else None
    rows = []
    for k in keys:
        b, a = before.get(k, Acc()), after.get(k, Acc())
        w_b = b.n / tb.n if tb.n else 0.0
        w_a = a.n / ta.n if ta.n else 0.0
        m_b, m_a = b.mean, a.mean
        rate = mix = None
        if a.n:
            if m_b is None:
                rate = w_a * (m_a - M_b) if M_b is not None else None
                mix = 0.0
            else:
                rate = w_a * (m_a - m_b)
        if M_b is not None and m_b is not None:
            mix = (w_a - w_b) * (m_b - M_b)
        elif mix is None and M_b is not None:
            mix = 0.0
        z = two_sided_z(b, a)
        rows.append({"key": k, "n_b": b.n, "n_a": a.n, "m_b": m_b, "m_a": m_a,
                     "delta": (m_a - m_b) if (m_a is not None and m_b is not None) else None,
                     "w_b": w_b, "w_a": w_a, "rate": rate, "mix": mix, "z": z,
                     "small": min(b.n, a.n) < min_n})
    for r in rows:
        r["share"] = (r["rate"] / total) if (total and abs(total) > 1e-12 and r["rate"] is not None) else None
        r["conc"] = bool(r["share"] is not None and r["share"] >= CONC_SHARE and not r["small"]
                         and r["z"] is not None and abs(r["z"]) >= Z)
    return {"dims": dims, "rows": rows, "M_b": M_b, "M_a": ta.mean, "total": total, "n_b": tb.n, "n_a": ta.n}


def weekly(obs, dims: tuple[str, ...], last_date: dt.date) -> tuple[list[dt.date], list[tuple], dict]:
    cells: dict[tuple, dict[dt.date, Acc]] = defaultdict(lambda: defaultdict(Acc))
    weeks = set()
    for o in obs:
        wk = o["date"] - dt.timedelta(days=o["date"].weekday())
        weeks.add(wk)
        key = tuple(o["seg"][d] for d in dims) if dims else ("все",)
        cells[key][wk].add(o["value"])
    return sorted(weeks), sorted(cells), cells


def fmt_val(x, binary: bool) -> str:
    if x is None:
        return "—"
    return c.pct(x) if binary else c.fmt(x)


def fmt_delta(x, binary: bool) -> str:
    if x is None:
        return "—"
    s = f"{c.fmt(100 * x, 1)} п. п." if binary else c.fmt(x)
    return ("+" if x > 0 else "") + s


def label(dims, key) -> str:
    return " × ".join(f"{d}={v}" for d, v in zip(dims, key))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Разрез метрики «сегмент × период» и поиск сегментов, где сосредоточено изменение.")
    p.add_argument("--csv", required=True, help="CSV (журнал событий или таблица наблюдений)")
    p.add_argument("--segments", required=True, help="столбцы сегментации через запятую: platform,channel")
    p.add_argument("--date-col", default="date", help="столбец даты (по умолчанию date)")
    p.add_argument("--value-col", help="[режим 2] столбец метрики (0/1 → доля; число → среднее)")
    p.add_argument("--user-col", default="user_id")
    p.add_argument("--event-col", default="event")
    p.add_argument("--base-event", help="[режим 1] событие, задающее базу (напр. onboarding_start)")
    p.add_argument("--target-event", help="[режим 1] целевое событие (напр. onboarding_complete)")
    p.add_argument("--split", help="дата границы YYYY-MM-DD: до < дата ≤ после")
    p.add_argument("--by", choices=["week"], help="разрез по неделям вместо до/после")
    p.add_argument("--depth", type=int, default=2, choices=[1, 2], help="1 — только одиночные столбцы; 2 — ещё и пары (по умолчанию)")
    p.add_argument("--min-n", type=int, default=30, help="минимум наблюдений в сегменте в каждом периоде (по умолчанию 30)")
    c.add_output_args(p)
    a = p.parse_args(argv)
    if not a.split and not a.by:
        p.error("нужен --split ДАТА или --by week")
    split = None
    if a.split:
        d = c.parse_date(a.split)
        if d is None:
            p.error(f"--split: не дата: {a.split!r}")
        split = d[0]

    obs, notes, nrows, _ = load_observations(a)
    if not obs:
        raise SystemExit("Ошибка: нет наблюдений (проверьте столбцы, даты и названия событий).")
    segs = [s.strip() for s in a.segments.split(",") if s.strip()]
    binary = is_binary(obs)
    levels = {s: sorted({o["seg"][s] for o in obs}) for s in segs}
    use = [s for s in segs if len(levels[s]) <= MAX_LEVELS]
    for s in segs:
        if s not in use:
            notes.append(f"столбец `{s}` пропущен: {len(levels[s])} значений (> {MAX_LEVELS}) — это не сегмент")
    combos = [(s,) for s in use]
    if a.depth == 2:
        combos += list(itertools.combinations(use, 2))
    dmin, dmax = min(o["date"] for o in obs), max(o["date"] for o in obs)
    metric = (f"доля пользователей с `{a.target_event}` среди имеющих `{a.base_event}`" if not a.value_col
              else f"{'доля' if binary else 'среднее'} по `{a.value_col}`")
    L = ["# Разрез «сегмент × период»", "", f"Метрика: {metric}. Наблюдений: {len(obs)}; даты {dmin} … {dmax}."]
    csv_rows: list[list] = []
    flagged: list[tuple] = []
    summary = ""

    if split:
        n_b = sum(1 for o in obs if o["date"] < split)
        n_a = len(obs) - n_b
        if not n_b or not n_a:
            raise SystemExit(f"Ошибка: по одну сторону даты {split} нет наблюдений (до: {n_b}, после: {n_a}).")
        res0 = decompose(obs, split, (), a.min_n)
        # общий итог
        tot = res0["rows"][0]
        L += ["", f"Период: до {split} — {n_b} набл., после (с {split}) — {n_a} набл.",
              f"Общая метрика: {fmt_val(tot['m_b'], binary)} → {fmt_val(tot['m_a'], binary)} "
              f"({fmt_delta(tot['delta'], binary)}).", ""]
        results = [decompose(obs, split, dims, a.min_n) for dims in combos]
        for res in results:
            dims = res["dims"]
            L += [f"## По `{'` × `'.join(dims)}`", "",
                  "| Сегмент | n до | n после | До | После | Изменение | Эффект ставки | Доля общего изменения | z | Пометка |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for r in sorted(res["rows"], key=lambda r: -abs(r["rate"] or 0)):
                mark = []
                if r["conc"]:
                    mark.append("**изменение сосредоточено здесь**")
                    flagged.append((r["share"], dims, r))
                if r["small"]:
                    mark.append(f"мало данных (< {a.min_n})")
                if r["n_b"] == 0:
                    mark.append("нет наблюдений до")
                share = "—" if r["share"] is None else f"{r['share']:.0%}"
                L.append(f"| {label(dims, r['key'])} | {r['n_b']} | {r['n_a']} | {fmt_val(r['m_b'], binary)} | "
                         f"{fmt_val(r['m_a'], binary)} | {fmt_delta(r['delta'], binary)} | "
                         f"{fmt_delta(r['rate'], binary)} | {share} | {c.fmt(r['z'], 1) if r['z'] is not None else '—'} | "
                         f"{'; '.join(mark)} |")
                csv_rows.append(["+".join(dims), label(dims, r["key"]), r["n_b"], r["n_a"], r["m_b"], r["m_a"],
                                 r["delta"], r["rate"], r["mix"], r["share"], r["z"], int(r["conc"])])
            mix_tot = sum(r["mix"] or 0 for r in res["rows"])
            if res["total"] is not None:
                L += ["", f"Сумма эффектов ставки: {fmt_delta(sum(r['rate'] or 0 for r in res['rows']), binary)}; "
                          f"сумма эффектов состава: {fmt_delta(mix_tot, binary)}; общее изменение {fmt_delta(res['total'], binary)}."]
            L.append("")
        # итог по сосредоточению: сильнейшие по доле, среди пар приоритет у более узкого с той же долей
        L += ["## Где сосредоточено изменение", ""]
        if flagged:
            seen = sorted(flagged, key=lambda t: (-t[0], len(t[1])))
            for sh, dims, r in seen[:6]:
                L.append(f"- {label(dims, r['key'])}: {fmt_val(r['m_b'], binary)} → {fmt_val(r['m_a'], binary)} "
                         f"({fmt_delta(r['delta'], binary)}), n {r['n_b']}→{r['n_a']}; "
                         f"{sh:.0%} общего изменения (эффект ставки), z = {c.fmt(r['z'], 1)}.")
            best = seen[0]
        else:
            L.append("- Ни один сегмент не объясняет ≥ 30 % изменения со значимой разностью: изменение распределено "
                     "или данных мало.")
            best = None
        calm = [(dims, r) for res in results for r in res["rows"]
                for dims in [res["dims"]] if len(dims) == 2 and not r["small"] and r["delta"] is not None
                and r["z"] is not None and abs(r["z"]) < Z]
        if calm:
            L += ["", "Контрольные сегменты (без значимого изменения, n достаточно): " +
                  "; ".join(f"{label(d, r['key'])} ({fmt_delta(r['delta'], binary)})" for d, r in calm[:6]) + "."]
        if best:
            sh, dims, r = best
            summary = (f"ИТОГ: общая метрика {fmt_val(tot['m_b'], binary)} → {fmt_val(tot['m_a'], binary)}; "
                       f"изменение сосредоточено в «{label(dims, r['key'])}» ({sh:.0%} общего, "
                       f"{fmt_val(r['m_b'], binary)} → {fmt_val(r['m_a'], binary)}); помеченных сегментов: {len(flagged)}; тип — estimate.")
        else:
            summary = (f"ИТОГ: общая метрика {fmt_val(tot['m_b'], binary)} → {fmt_val(tot['m_a'], binary)}; "
                       f"концентрации изменения в одном сегменте не найдено; тип — estimate.")
        csv_head = ["partition", "segment", "n_before", "n_after", "m_before", "m_after", "delta", "rate_effect",
                    "mix_effect", "share_of_change", "z", "concentrated"]
    else:
        last_week_partial = dmax.weekday() != 6
        weeks, _, _ = weekly(obs, (), dmax)
        L += ["", "Период: недели (с понедельника). "
              + (f"Последняя неделя ({weeks[-1]}) неполная — данные до {dmax}." if last_week_partial else "")]
        csv_head = ["partition", "segment", "week", "n", "metric"]
        for dims in [()] + [(s,) for s in use]:
            wk, keys, cells = weekly(obs, dims, dmax)
            L += ["", f"## По `{'`'.join(dims) if dims else 'всем'}`", "",
                  "| Неделя | " + " | ".join(label(dims, k) if dims else "все" for k in keys) + " |",
                  "|---|" + "---|" * len(keys)]
            for w in wk:
                cells_txt = []
                for k in keys:
                    acc = cells[k].get(w)
                    cells_txt.append("—" if not acc else f"{fmt_val(acc.mean, binary)} (n={acc.n})")
                    if acc:
                        csv_rows.append(["+".join(dims) or "all", label(dims, k) if dims else "all", str(w), acc.n, acc.mean])
                L.append(f"| {w} | " + " | ".join(cells_txt) + " |")
        summary = f"ИТОГ: метрика по неделям {weeks[0]} … {weeks[-1]} ({len(weeks)} нед.), сегменты: {', '.join(use)}; тип — estimate."

    L += ["", "## Замечания", "",
          "- Связь, не причинность: два сегмента, совпавшие по времени (платформа/версия, канал), по одному разрезу не разделить — "
          "смотрите парные разрезы и контрольные сегменты, затем проверяйте экспериментом.",
          "- Поздние периоды могут иметь неполное окно наблюдения (правое цензурирование): сравнивайте метрики с одинаковым окном.",
          "- z — нормальное приближение для разности двух долей/средних; при множестве сегментов часть «значимых» разностей случайна."]
    L += [f"- {n}" for n in notes]
    L += ["", summary, "", c.provenance_md("segment.py", [(a.csv, nrows)], vars(a))]
    out = c.resolve_out(a, f"segment_{Path(a.csv).stem}.md")
    if out:
        c.write_csv(out.with_suffix(".csv"), csv_head, csv_rows)
    c.finish(out, "\n".join(L), summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
