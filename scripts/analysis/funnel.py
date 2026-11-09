#!/usr/bin/env python3
"""Воронка и конверсии.

Режим 1 — события (пользователь, шаг):
  python3 scripts/analysis/funnel.py --events ev.csv --steps visit,signup,trial,pay --out projects/<slug>/data/funnel.md
  Пользователь засчитывается на шаге k, только если был на всех предыдущих шагах (строгая воронка);
  для сравнения выводится и «без учёта предыдущих шагов».
Режим 2 — агрегаты (step, users) в порядке воронки:
  python3 scripts/analysis/funnel.py --agg funnel_agg.csv --out projects/<slug>/data/funnel.md
Для каждой конверсии — 95 % интервал Уилсона.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def from_events(path, steps, user_col="user_id", step_col="step") -> dict:
    header, rows, _ = c.read_csv(path)
    c.require_columns(header, [user_col, step_col], path)
    seen: dict[str, set[str]] = defaultdict(set)
    unknown: dict[str, int] = defaultdict(int)
    for r in rows:
        if c.is_missing(r[user_col]):
            continue
        s = r[step_col]
        if s in steps:
            seen[r[user_col]].add(s)
        else:
            unknown[s] += 1
    strict, loose = [], []
    for i, s in enumerate(steps):
        strict.append(sum(1 for got in seen.values() if all(x in got for x in steps[: i + 1])))
        loose.append(sum(1 for got in seen.values() if s in got))
    notes = []
    if unknown:
        notes.append(f"события вне списка шагов (не учтены): {dict(unknown)}")
    skipped = [f"{s}: {l - st}" for s, st, l in zip(steps, strict, loose) if l > st]
    if skipped:
        notes.append("пользователи, попавшие на шаг в обход предыдущих (возможны другие входы в воронку или потеря событий): "
                     + ", ".join(skipped))
    return build(steps, strict, notes, loose=loose, rows=len(rows))


def from_agg(path, step_col="step", users_col="users") -> dict:
    header, rows, _ = c.read_csv(path)
    c.require_columns(header, [step_col, users_col], path)
    steps, counts = [], []
    for r in rows:
        x = c.parse_number(r[users_col])
        if x is None:
            raise SystemExit(f"Ошибка: нет числа в строке {r}")
        steps.append(r[step_col])
        counts.append(int(x))
    return build(steps, counts, [], rows=len(rows))


def build(steps, counts, notes, loose=None, rows=None) -> dict:
    out = []
    for i, (s, n) in enumerate(zip(steps, counts)):
        prev = counts[i - 1] if i else None
        row = {"step": s, "users": n,
               "conv_prev": (n / prev) if prev else None, "ci_prev": wilson(n, prev) if prev and n <= prev else None,
               "conv_first": (n / counts[0]) if counts[0] else None,
               "loose": loose[i] if loose else None}
        if prev is not None and n > prev:
            notes.append(f"шаг «{s}» ({n}) больше предыдущего ({prev}) — воронка не может расти: разные периоды, "
                         f"определения или источники данных")
        out.append(row)
    drops = [(r["step"], out[i - 1]["step"], 1 - r["conv_prev"]) for i, r in enumerate(out) if i and r["conv_prev"] is not None]
    worst = max(drops, key=lambda x: x[2]) if drops else None
    return {"steps": out, "notes": notes, "worst": worst, "rows": rows}


def render(res, prov) -> tuple[str, str]:
    st = res["steps"]
    has_loose = st[0]["loose"] is not None
    L = ["# Воронка", "", "| Шаг | Пользователей | Конверсия из предыдущего (95 % ДИ) | От первого шага |"
         + (" Без учёта предыдущих шагов |" if has_loose else ""),
         "|---|---|---|---|" + ("---|" if has_loose else "")]
    for r in st:
        ci = f" ({c.pct(r['ci_prev'][0])} – {c.pct(r['ci_prev'][1])})" if r["ci_prev"] else ""
        L.append(f"| {r['step']} | {r['users']} | {c.pct(r['conv_prev'])}{ci} | {c.pct(r['conv_first'])} |"
                 + (f" {r['loose']} |" if has_loose else ""))
    L += ["", "## Замечания к данным", ""] + ([f"- {x}" for x in res["notes"]] or ["- нет"])
    first, last = st[0], st[-1]
    itog = (f"ИТОГ: сквозная конверсия {first['step']}→{last['step']}: {c.pct(last['conv_first'])} "
            f"({last['users']} из {first['users']})")
    if res["worst"]:
        itog += f"; наибольшие потери {res['worst'][1]}→{res['worst'][0]}: −{c.pct(res['worst'][2])}"
    itog += "."
    L += ["", itog, "", prov]
    return "\n".join(L), itog


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Воронка: пользователи по шагам и конверсии.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--events", help="CSV событий: пользователь, шаг")
    src.add_argument("--agg", help="CSV агрегатов: step, users (в порядке воронки)")
    p.add_argument("--steps", help="порядок шагов через запятую (обязателен для --events)")
    p.add_argument("--user-col", default="user_id")
    p.add_argument("--step-col", default="step")
    p.add_argument("--users-col", default="users")
    c.add_output_args(p)
    a = p.parse_args(argv)
    path = a.events or a.agg
    if a.events:
        if not a.steps:
            p.error("для --events нужен --steps")
        res = from_events(a.events, [s.strip() for s in a.steps.split(",")], a.user_col, a.step_col)
    else:
        res = from_agg(a.agg, a.step_col, a.users_col)
    out = c.resolve_out(a, f"funnel_{Path(path).stem}.md")
    report, itog = render(res, c.provenance_md("funnel.py", [(path, res["rows"])], vars(a)))
    if out:
        c.write_csv(out.with_suffix(".csv"), ["step", "users", "conv_prev", "conv_first"],
                    [[r["step"], r["users"], "" if r["conv_prev"] is None else round(r["conv_prev"], 4),
                      "" if r["conv_first"] is None else round(r["conv_first"], 4)] for r in res["steps"]])
    c.finish(out, report, f"{itog} sha256 {c.sha256_file(path)[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
