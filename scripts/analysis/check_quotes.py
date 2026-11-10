#!/usr/bin/env python3
"""Проверка выхода сборщика: каждая цитата из evidence.jsonl дословно есть в тексте источника,
а числа из утверждения есть в цитате. Детерминированно, без модели — проверяем 100 % записей.

  python3 scripts/analysis/check_quotes.py <slug> --ids E011,E012,E013
  python3 scripts/analysis/check_quotes.py <slug> --since E011
  python3 scripts/analysis/check_quotes.py <slug> --sample 5     # + случайная выборка для смысловой сверки

Сравнение нечувствительно к регистру, пробелам, виду кавычек и тире, ё/е; «…» или «...» в цитате
делит её на фрагменты, которые должны идти в источнике по порядку.
Для расчётов (type=estimate) цитата может быть из отчёта в data/ — тогда locator = путь к отчёту.
Вывод: ОШИБКИ (цитата не найдена, нет текста источника, fact без цитаты) и ПРЕДУПРЕЖДЕНИЯ
(содержательное число утверждения не найдено в цитате/locator/названии источника, длина > 300).
Код выхода: 1 — только при ОШИБКАХ (порог 20 % → повтор на сильной модели, см. скилл research).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TRANS = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "'": '"', "’": '"', "‘": '"',
                        "—": "-", "–": "-", "−": "-", "ё": "е", " ": " ", " ": " "})
_SP = "[   ]"
NUM = re.compile(r"(?<![\w.,])(\d+(?:" + _SP + r"\d{3})*(?:[.,]\d+)?)")
# единицы, после которых число — содержательная величина
UNIT = re.compile(r"\s?(%|₽|\$|€|руб|р\.|долл|евро|тыс|млн|млрд|трлн|[гмкт]б(?!\w)|[гмкт]байт|[гмк]бит|раз(?:а)?(?!\w)"
                  r"|п\.\s?п\.|шт|чел|пользовател|абонент|клиент|дн|день|сут|недел|мес|час|мин|сек|лет(?!\w)|год|стран|sms|смс)",
                  re.I)
STRONG = re.compile(r"\s?(%|₽|\$|€|руб|долл|евро|тыс|млн|млрд|трлн|[гмкт]б(?!\w))", re.I)
SCALE = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
SCALE_RX = re.compile(r"\s?(тыс|млн|млрд|трлн)", re.I)
# номер статьи/пункта/части и т. п. перед числом
REF_BEFORE = re.compile(r"(?:\bст\.?|стать\w*|\bч\.|част\w*|\bп\.|\bпп\.|пункт\w*|№|\bгл\.|глав\w*|абз\.?|раздел\w*|"
                        r"\bразд\.|подп\.?|\bстр\.|\bс\.|\bтабл\.?|рис\.?)\s*$", re.I)
MONTHS = re.compile(r"\s?(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр|янв|фев|мар|апр|авг|сен|окт|ноя|дек)", re.I)


def _value(tok: str, after: str) -> set[float]:
    """Значения числа: как записано и с учётом «тыс/млн/млрд» (29,18 млн = 29 180 000)."""
    x = float(re.sub(_SP, "", tok).replace(",", "."))
    vals = {x}
    m = SCALE_RX.match(after)
    if m:
        vals.add(x * SCALE[m.group(1).lower()])
    return {float(f"{v:.9g}") for v in vals}


def substantive_numbers(claim: str) -> list[tuple[str, set[float]]]:
    """Содержательные числа утверждения: с единицей (%, ₽, млн, ГБ, раз, дней…) или из ≥ 3 цифр.
    Пропускаются: части названий (Т2, esim365, 5G), номера статей/пунктов (ст. 44.1, 38-ФЗ),
    даты (12.03.2025, 9 июня) и годы 1990–2035."""
    out = []
    for m in NUM.finditer(claim):
        tok, after, before = m.group(1), claim[m.end():m.end() + 20], claim[max(0, m.start() - 15):m.start()]
        if re.match(r"[^\W\d_]", after) and not UNIT.match(after):
            continue  # приклеено к буквам: 5G, 4G
        if re.match(r"-[^\W\d_]", after) or REF_BEFORE.search(before):
            continue  # 38-ФЗ, ст. 5, п. 1
        has_unit = bool(UNIT.match(after))
        if MONTHS.match(after) or (re.fullmatch(r"\d{1,2}\.\d{2}", tok) and (after.startswith(".") or not has_unit)):
            continue  # даты
        digits = re.sub(r"\D", "", tok.split(",")[0].split(".")[0])
        if re.fullmatch(r"(199\d|20[0-2]\d|203[0-5])", re.sub(_SP, "", tok)) and not STRONG.match(after):
            continue  # год
        if has_unit or len(digits) >= 3:
            out.append((tok, _value(tok, after)))
    return out


def all_values(text: str) -> set[float]:
    vals = set()
    for m in re.finditer(r"(\d+(?:" + _SP + r"\d{3})*(?:[.,]\d+)?)", text):
        try:
            vals |= _value(m.group(1), text[m.end():m.end() + 8])
        except ValueError:
            pass
    return vals


def missing_numbers(claim: str, quote: str, context: str = "") -> list[str]:
    """Содержательные числа утверждения, которых нет ни в цитате, ни в locator/названии источника."""
    have = all_values(quote) | all_values(context)
    return [tok for tok, vals in substantive_numbers(claim) if not (vals & have)]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().translate(_TRANS)).strip()


def quote_in(quote: str, text: str) -> bool:
    t = norm(text)
    pos = 0
    for frag in re.split(r"…|\.\.\.", quote):
        f = norm(frag).strip(" .,;:\"")
        if not f:
            continue
        i = t.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def load_jsonl(path: Path) -> list[dict]:
    """Чтение реестра через store.read_jsonl (битые строки пропускаются, как во всём проекте)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from lib import store  # noqa: PLC0415
    return store.read_jsonl(path)


def source_files(project: Path, sid: str, src: dict | None) -> list[Path]:
    """Где искать текст источника: sources/S###.md; для kind=table — таблица в data/ (или по locator)."""
    out = []
    f = project / "sources" / f"{sid}.md"
    if f.is_file():
        out.append(f)
    if src and src.get("kind") == "table":
        loc = (src.get("locator") or "").split("#")[0].strip()
        cands = []
        if loc:
            cands += [project / loc, project / "data" / Path(loc).name]
        for cand in cands:
            try:
                if cand.is_file() and cand.resolve().is_relative_to(project.resolve()) and cand not in out:
                    out.append(cand)
            except OSError:
                pass
    return out


def id_num(eid: str) -> int:
    return int(re.sub(r"\D", "", eid) or 0)


def check(project: Path, ids=None, since=None) -> list[dict]:
    ev = [e for e in load_jsonl(project / "evidence.jsonl") if e.get("status", "active") == "active"]
    if ids:
        ev = [e for e in ev if e["id"] in ids]
    if since:
        ev = [e for e in ev if id_num(e["id"]) >= id_num(since)]
    srcs_all = {x["id"]: x for x in load_jsonl(project / "sources.jsonl")}
    titles = {k: x.get("title", "") + " " + x.get("locator", "") for k, x in srcs_all.items()}
    results = []
    for e in ev:
        r = {"id": e["id"], "type": e.get("type"), "claim": e.get("claim", ""), "quote": e.get("quote") or "",
             "problems": [], "warnings": []}
        srcs = e.get("source_ids") or []
        if e.get("type") == "fact" and (not srcs or not r["quote"]):
            r["problems"].append("fact без источника или цитаты")
        if r["quote"] and srcs:
            texts = []
            for s in srcs:
                files = source_files(project, s, srcs_all.get(s))
                if files:
                    texts.extend(f.read_text(encoding="utf-8", errors="replace") for f in files)
                else:
                    r["problems"].append(f"нет текста источника sources/{s}.md"
                                         + (" и таблицы в data/" if (srcs_all.get(s) or {}).get("kind") == "table" else ""))
            loc = e.get("locator") or ""
            report = project / loc.split("#")[0] if loc else None
            if report and report.is_file() and e.get("type") == "estimate":
                texts.append(report.read_text(encoding="utf-8"))
            if texts and not any(quote_in(r["quote"], t) for t in texts):
                r["problems"].append("цитата не найдена в тексте источника")
            if len(r["quote"]) > 300:
                r["warnings"].append(f"цитата длиннее 300 символов ({len(r['quote'])})")
        if r["quote"] and e.get("type") == "fact":
            ctx = " ".join([e.get("locator") or "", str(e.get("data_date") or "")] + [titles.get(x, "") for x in srcs])
            miss = missing_numbers(r["claim"], r["quote"], ctx)
            if miss:
                r["warnings"].append(f"числа из утверждения нет в цитате: {miss}")
        if e.get("type") == "synthetic" and re.search(r"(?i)доля|большинство|спрос|\d+\s*%", r["claim"]):
            r["warnings"].append("synthetic не может доказывать долю/спрос — переформулируйте")
        results.append(r)
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Сверка цитат evidence с текстами источников.")
    p.add_argument("slug", nargs="?", help="проект в projects/")
    p.add_argument("--project-dir", help="путь к папке проекта (вместо slug)")
    p.add_argument("--ids", help="E011,E012 — проверить только эти")
    p.add_argument("--since", help="E011 — проверить начиная с этого ID")
    p.add_argument("--sample", type=int, default=0, help="вывести N случайных записей для смысловой сверки")
    p.add_argument("--seed", type=int, default=0, help="зерно выборки (воспроизводимость)")
    a = p.parse_args(argv)
    if not (a.slug or a.project_dir):
        p.error("укажите slug или --project-dir")
    if a.project_dir:
        project = Path(a.project_dir)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _common as c  # noqa: PLC0415
        project = c.project_path(a.slug)
    ids = {x.strip() for x in a.ids.split(",")} if a.ids else None
    res = check(project, ids, a.since)
    bad = [r for r in res if r["problems"]]
    warn = [r for r in res if r["warnings"]]
    if bad:
        print("ОШИБКИ (цитата не подтверждена источником — исправить или отозвать):")
        for r in bad:
            print(f"- {r['id']}: " + "; ".join(r["problems"]))
    if warn:
        print("ПРЕДУПРЕЖДЕНИЯ (проверить вручную: утверждение шире цитаты?):")
        for r in warn:
            print(f"- {r['id']}: " + "; ".join(r["warnings"]))
    if a.sample and res:
        print("\nСмысловая сверка (прочитай: утверждение не говорит больше, чем цитата? тип и период верны?):")
        for r in random.Random(a.seed).sample(res, min(a.sample, len(res))):
            print(f"- {r['id']} [{r['type']}] {r['claim']}\n    цитата: «{r['quote']}»")
    share = len(bad) / len(res) if res else 0.0
    print(f"\nИТОГ: проверено {len(res)}, ошибок {len(bad)} ({share:.0%}), предупреждений {len(warn)}."
          + (" Порог 20 % превышен — повторить сбор на сильной модели." if share > 0.2 else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
