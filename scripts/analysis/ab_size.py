#!/usr/bin/env python3
"""Размер выборки и срок A/B-теста для конверсии (две доли, z-тест).

  python3 scripts/analysis/ab_size.py --baseline 4.2% --mde-rel 10% --daily 5000 --out projects/<slug>/data/ab_size.md

n на группу = (z(1−α/2)·√(2·p̄(1−p̄)) + z(1−β)·√(p1(1−p1) + p2(1−p2)))² / (p2 − p1)²,
p̄ = (p1 + p2) / 2. При >2 вариантах α делится на число сравнений с контролем (Бонферрони).
Срок округляется вверх до целых недель (недельная сезонность), минимум 1 неделя.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

MAX_DAYS_WARN = 56


def sample_size(p1: float, mde_abs: float, alpha=0.05, power=0.8, two_sided=True, comparisons=1) -> int:
    p2 = p1 + mde_abs
    if not (0 < p1 < 1 and 0 < p2 < 1) or mde_abs == 0:
        raise ValueError("базовая конверсия и конверсия с эффектом должны быть в (0; 1), эффект ≠ 0")
    a = alpha / comparisons
    z_a = NormalDist().inv_cdf(1 - a / 2 if two_sided else 1 - a)
    z_b = NormalDist().inv_cdf(power)
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / mde_abs ** 2)


def duration(n_per_group: int, variants: int, daily: float | None, share: float = 1.0) -> dict | None:
    if not daily:
        return None
    total = n_per_group * variants
    days = math.ceil(total / (daily * share))
    weeks = max(1, math.ceil(days / 7))
    return {"total": total, "days_raw": days, "weeks": weeks, "days": weeks * 7}


def plan(baseline, mde_abs, alpha, power, two_sided, variants, daily, share) -> dict:
    comps = max(1, variants - 1)
    n = sample_size(baseline, mde_abs, alpha, power, two_sided, comps)
    return {"n": n, "dur": duration(n, variants, daily, share), "comparisons": comps}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Размер выборки и срок A/B-теста по конверсии.")
    p.add_argument("--baseline", required=True, type=c.share_type, help="базовая конверсия: 0.042 или 4.2%%")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--mde-rel", type=c.share_type, help="минимальный эффект, относительный: 0.1 или 10%% (4,2 %% → 4,62 %%)")
    g.add_argument("--mde-abs", type=c.share_type, help="минимальный эффект в п. п. как доля: 0.005 = +0,5 п. п.")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--one-sided", action="store_true", help="односторонний тест (только если ухудшение не интересует)")
    p.add_argument("--variants", type=int, default=2, help="число групп вместе с контролем")
    p.add_argument("--daily", type=float, help="пользователей в день, которые могут попасть в тест (все группы вместе)")
    p.add_argument("--share", default="1", type=c.share_type, help="доля этого трафика, отдаваемая в тест: 0.5 или 50%%")
    c.add_output_args(p)
    a = p.parse_args(argv)

    base = a.baseline
    mde_abs = base * a.mde_rel if a.mde_rel is not None else a.mde_abs
    share = a.share
    if a.variants < 2:
        p.error("--variants ≥ 2")
    res = plan(base, mde_abs, a.alpha, a.power, not a.one_sided, a.variants, a.daily, share)

    L = ["# Размер выборки A/B-теста", "", "| Параметр | Значение |", "|---|---|",
         f"| Базовая конверсия | {c.pct(base, 2)} |",
         f"| Минимальный обнаруживаемый эффект (MDE) | +{c.fmt(mde_abs * 100, 2)} п. п. → {c.pct(base + mde_abs, 2)} "
         f"(относительно {c.pct(mde_abs / base)}) |",
         f"| α / мощность | {a.alpha} ({'одностор.' if a.one_sided else 'двустор.'}"
         f"{', поправка Бонферрони на ' + str(res['comparisons']) + ' сравн.' if res['comparisons'] > 1 else ''}) / {a.power} |",
         f"| Групп | {a.variants} |",
         f"| **Нужно на группу** | **{c.fmt(float(res['n']), 0)}** |"]
    d = res["dur"]
    if d:
        L += [f"| Всего в тесте | {c.fmt(float(d['total']), 0)} |",
              f"| Трафик в тест | {c.fmt(a.daily * share, 0)} в день |",
              f"| **Срок** | **{d['days']} дней ({d['weeks']} полн. нед.)**; по расчёту {d['days_raw']} дн. |"]
    L += ["", "## Как меняется срок от MDE", "", "| MDE (отн.) | MDE (п. п.) | На группу | Срок, дней |", "|---|---|---|---|"]
    for f in (0.5, 1.0, 1.5, 2.0):
        m = mde_abs * f
        if base + m >= 1:
            continue
        r = plan(base, m, a.alpha, a.power, not a.one_sided, a.variants, a.daily, share)
        L.append(f"| {c.pct(m / base)} | {c.fmt(m * 100, 2)} | {c.fmt(float(r['n']), 0)} | {r['dur']['days'] if r['dur'] else '—'} |")
    warn = []
    if d and d["days"] > MAX_DAYS_WARN:
        warn.append(f"срок {d['days']} дн. > {MAX_DAYS_WARN}: увеличьте MDE, долю трафика, возьмите более чувствительную "
                    f"метрику или другой дизайн (см. knowledge/methods/research/test-design.md)")
    if d and d["days_raw"] < 7:
        warn.append("расчётно меньше недели — всё равно держите тест полную неделю (лучше две): недельная сезонность и эффект новизны")
    if not d:
        warn.append("срок не посчитан: задайте --daily (трафик в день)")
    warn.append("срок и критерий остановки фиксируются до старта; досрочная остановка при «значимости» даёт ложные победы")
    warn.append("проверяйте SRM: фактическое соотношение групп должно совпадать с плановым")
    L += ["", "## Предупреждения", ""] + [f"- {w}" for w in warn]
    itog = (f"ИТОГ: при базе {c.pct(base, 2)} и MDE +{c.fmt(mde_abs * 100, 2)} п. п. нужно {res['n']} на группу"
            + (f", срок {d['days']} дней ({d['weeks']} нед.)" if d else "") + f" при α={a.alpha}, мощности {a.power}.")
    L += ["", itog, "", c.provenance_md("ab_size.py", [], vars(a))]
    out = c.resolve_out(a, "ab_size.md")
    c.finish(out, "\n".join(L), itog)
    return 0


if __name__ == "__main__":
    sys.exit(main())
