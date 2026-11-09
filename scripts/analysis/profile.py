#!/usr/bin/env python3
"""Профиль таблицы: столбцы, типы, пропуски, диапазоны дат, дубли и подозрения
на смешение единиц/периодов. Результат — markdown-отчёт.

Пример:
  python3 scripts/analysis/profile.py data.csv --key user_id,date --out projects/<slug>/data/profile_data.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as c  # noqa: E402

UNIT_TOKENS = {
    "тыс.": re.compile(r"(?i)тыс"), "млн": re.compile(r"(?i)млн"), "млрд": re.compile(r"(?i)млрд"),
    "руб.": re.compile(r"(?i)руб|₽"), "$": re.compile(r"\$|usd", re.I), "%": re.compile(r"%"),
}
CUMULATIVE = re.compile(r"(?i)(нараст|ytd|с начала года|\b(3|6|9|12)\s*[мm]\b|\b[1-4]\s*[hпн]\b)")
SHARE_NAME = re.compile(r"(?i)(%|доля|share|rate|conv|конв|процент|retention|удерж|churn|отток)")
TYPE_THRESHOLD = 0.95


def infer_type(values: list[str]) -> tuple[str, int]:
    """Тип по непустым значениям: date | integer | number | text. Возвращает (тип, число несовпавших)."""
    if not values:
        return "empty", 0
    n = len(values)
    dates = sum(1 for v in values if c.parse_date(v))
    if dates / n >= TYPE_THRESHOLD:
        return "date", n - dates
    nums = [c.parse_number(v) for v in values]
    ok = [x for x in nums if x is not None]
    if len(ok) / n >= TYPE_THRESHOLD:
        kind = "integer" if all(float(x).is_integer() for x in ok) else "number"
        return kind, n - len(ok)
    return "text", 0


def iqr_outliers(xs: list[float]) -> int:
    if len(xs) < 8:
        return 0
    q1, _, q3 = statistics.quantiles(xs, n=4)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return sum(1 for x in xs if x < lo or x > hi)


def magnitude_gap(xs: list[float]) -> tuple[float, float, float] | None:
    """Ищет разрыв ≥ 100× между соседними (по величине) положительными значениями,
    когда по обе стороны ≥ 5 % значений: типичный след смешения руб./тыс. руб."""
    pos = sorted(x for x in xs if x > 0)
    if len(pos) < 10:
        return None
    best = None
    for i in range(1, len(pos)):
        ratio = pos[i] / pos[i - 1]
        share_low = i / len(pos)
        if ratio >= 100 and 0.05 <= share_low <= 0.95 and (best is None or ratio > best[0]):
            best = (ratio, share_low, pos[i - 1])
    return best


def month_index(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def profile_column(name: str, raw: list[str]) -> dict:
    vals = [v for v in raw if not c.is_missing(v)]
    col = {"name": name, "n": len(raw), "missing": len(raw) - len(vals),
           "zeros_as_text": sum(1 for v in raw if v.strip() in ("0", "0,0", "0.0")),
           "missing_tokens": Counter(v.strip() for v in raw if c.is_missing(v)), "flags": []}
    col["type"], col["bad"] = infer_type(vals)
    col["unique"] = len(set(vals))
    if col["bad"]:
        examples = [v for v in vals if (c.parse_date(v) if col["type"] == "date" else c.parse_number(v)) is None][:3]
        col["flags"].append(f"{col['bad']} значений не распознаны как {col['type']}: {examples}")
    tokens = set(col["missing_tokens"]) - {""}
    if tokens and "" in col["missing_tokens"]:
        col["flags"].append(f"пропуски записаны по-разному: пусто и {sorted(tokens)} — проверьте, не значат ли они разное")
    if col["type"] in ("integer", "number"):
        xs = [x for x in (c.parse_number(v) for v in vals) if x is not None]
        col.update(min=min(xs), max=max(xs), median=statistics.median(xs), mean=statistics.fmean(xs),
                   negatives=sum(1 for x in xs if x < 0), outliers=iqr_outliers(xs))
        units = {u for u, rx in UNIT_TOKENS.items() for v in vals if rx.search(v)}
        if units:
            with_unit = sum(1 for v in vals if any(UNIT_TOKENS[u].search(v) for u in units))
            if with_unit < len(vals):
                col["flags"].append(f"единицы {sorted(units)} указаны только в {with_unit} из {len(vals)} значений — возможно смешение единиц")
            else:
                col["flags"].append(f"единицы в значениях: {sorted(units)} — учтите при расчётах")
        gap = magnitude_gap(xs)
        if gap:
            col["flags"].append(f"разрыв величин в {gap[0]:.0f}×: {gap[1]:.0%} значений ≤ {c.fmt(gap[2])}, остальные на 2+ порядка больше — "
                                f"подозрение на смешение единиц (руб./тыс. руб.) или разных сущностей")
        nonzero = [x for x in xs if x != 0]
        small = sum(1 for x in nonzero if 0 < abs(x) <= 1)
        mid = sum(1 for x in nonzero if 1 < abs(x) <= 100)
        if SHARE_NAME.search(name) and small and mid and max(abs(x) for x in xs) <= 100:
            col["flags"].append(f"доли в разных шкалах: {small} значений в 0–1 и {mid} в 1–100 — смешение долей и процентов")
        if col["outliers"]:
            col["flags"].append(f"{col['outliers']} выбросов по правилу IQR — проверьте: ошибка ввода, событие или другая сущность")
    elif col["type"] == "date":
        parsed = [c.parse_date(v) for v in vals]
        parsed = [p for p in parsed if p]
        dates = [p[0] for p in parsed]
        grans = Counter(p[1] for p in parsed)
        col.update(min=min(dates), max=max(dates), granularity=dict(grans))
        if len(grans) > 1:
            col["flags"].append(f"смешение периодов в одном столбце: {dict(grans)}")
        day_level = grans.get("day", 0) == len(parsed)
        if day_level and all(d.day == 1 for d in dates) and len(set(dates)) > 1:
            col["granularity"] = {"month (1-е числа)": len(parsed)}
            grans = Counter({"month": len(parsed)})
        if set(grans) == {"month"} or set(grans) == {"quarter"}:
            step = 3 if "quarter" in grans else 1
            have = {month_index(d) for d in dates}
            want = range(min(have), max(have) + 1, step)
            gaps = [f"{m // 12}-{m % 12 + 1:02d}" for m in want if m not in have]
            col["period_gaps"] = gaps
            if gaps:
                col["flags"].append(f"пропущены периоды ({len(gaps)}): {gaps[:10]}")
        elif day_level:
            span = (max(dates) - min(dates)).days + 1
            uniq = len(set(dates))
            col["period_gaps"] = span - uniq
            if uniq > 20 and span - uniq > 0.1 * span:
                col["flags"].append(f"в диапазоне {span} дней нет данных за {span - uniq} дней")
        today = dt.date.today()
        future = sum(1 for d in dates if d > today)
        if future:
            col["flags"].append(f"{future} дат в будущем (позже {today})")
        last = max(dates)
        if set(grans) == {"month"} and month_index(last) == month_index(today):
            col["flags"].append("последний месяц — текущий, вероятно неполный: не сравнивайте его с полными")
    else:
        col["top"] = Counter(vals).most_common(5)
        cum = sum(1 for v in vals if CUMULATIVE.search(v))
        if cum:
            col["flags"].append(f"{cum} значений похожи на нарастающий итог/полугодия (напр. «9М», «YTD») — проверьте тип периода")
    if col["n"] and col["missing"] / col["n"] > 0.2:
        col["flags"].append(f"пропусков {col['missing'] / col['n']:.0%} > 20 % — выводы по столбцу не выше medium")
    return col


def profile(path: str, key: list[str] | None = None) -> dict:
    header, rows, meta = c.read_csv(path)
    cols = [profile_column(h, [r[h] for r in rows]) for h in header]
    tuples = [tuple(r[h] for h in header) for r in rows]
    dup_rows = len(tuples) - len(set(tuples))
    res = {"path": path, "rows": len(rows), "header": header, "meta": meta, "columns": cols,
           "duplicate_rows": dup_rows, "flags": []}
    if meta["ragged_rows"]:
        res["flags"].append(f"{meta['ragged_rows']} строк с другим числом полей, чем в заголовке — возможен сдвиг столбцов")
    if dup_rows:
        res["flags"].append(f"{dup_rows} полных дублей строк")
    if key:
        c.require_columns(header, key, path)
        keys = [tuple(r[k] for k in key) for r in rows]
        res["duplicate_keys"] = len(keys) - len(set(keys))
        if res["duplicate_keys"]:
            res["flags"].append(f"{res['duplicate_keys']} повторов ключа {key}")
    for col in cols:
        hint = re.search(r"(?i)\((.*?)\)|,\s*(тыс|млн|млрд|руб|%)", col["name"])
        if hint:
            col["unit_hint"] = hint.group(0)
    res["n_flags"] = len(res["flags"]) + sum(len(x["flags"]) for x in cols)
    return res


def render(res: dict, prov: str) -> str:
    L = [f"# Профиль таблицы `{Path(res['path']).name}`", "",
         f"Строк данных: {res['rows']}; столбцов: {len(res['header'])}; кодировка {res['meta']['encoding']}, "
         f"разделитель `{res['meta']['delimiter']!r}`; полных дублей строк: {res['duplicate_rows']}"
         + (f"; повторов ключа: {res['duplicate_keys']}" if "duplicate_keys" in res else "") + ".", "",
         "## Столбцы", "", "| Столбец | Тип | Пропуски | Уникальных | Диапазон / частые значения |", "|---|---|---|---|---|"]
    for col in res["columns"]:
        miss = f"{col['missing']} ({col['missing'] / col['n']:.0%})" if col["n"] else "0"
        if col["type"] in ("integer", "number"):
            rng = f"{c.fmt(col['min'])} … {c.fmt(col['max'])}, медиана {c.fmt(col['median'])}"
        elif col["type"] == "date":
            rng = f"{col['min']} … {col['max']}; {col['granularity']}"
        elif col["type"] == "text":
            rng = "; ".join(f"{v} ({n})" for v, n in col["top"])
        else:
            rng = "—"
        name = col["name"] + (f" — единица в названии: {col['unit_hint']}" if col.get("unit_hint") else "")
        L.append(f"| {name} | {col['type']} | {miss} | {col['unique']} | {rng} |")
    L += ["", "## Подозрения и что проверить", ""]
    flags = [f"- Таблица: {f}" for f in res["flags"]] + [f"- `{x['name']}`: {f}" for x in res["columns"] for f in x["flags"]]
    L += flags or ["- Явных проблем не найдено. Всё равно сверьте определения показателей и тип периода (поток/на дату)."]
    L += ["", f"ИТОГ: таблица {Path(res['path']).name}: {res['rows']} строк, {len(res['header'])} столбцов, "
              f"подозрений: {res['n_flags']}.", "", prov]
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Профиль CSV-таблицы: типы, пропуски, даты, дубли, единицы.")
    p.add_argument("file", help="CSV-файл")
    p.add_argument("--key", help="столбцы ключа через запятую для поиска повторов (напр. user_id,date)")
    c.add_output_args(p)
    a = p.parse_args(argv)
    key = [k.strip() for k in a.key.split(",")] if a.key else None
    res = profile(a.file, key)
    out = c.resolve_out(a, f"profile_{Path(a.file).stem}.md")
    report = render(res, c.provenance_md("profile.py", [(a.file, res["rows"])], vars(a)))
    summary = report.split("ИТОГ: ", 1)[1].split("\n", 1)[0]
    c.finish(out, report, f"ИТОГ: {summary} sha256 {c.sha256_file(a.file)[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
