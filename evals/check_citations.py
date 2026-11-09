#!/usr/bin/env python3
"""Выборочная проверка цитат: есть ли quote из evidence (type=fact) в тексте источника.

Для N случайных evidence типа fact (по умолчанию только status=active) ищет поле quote
в sources/<S###>.md каждого из source_ids. Сравнение после нормализации: регистр, пробелы,
ё→е, типографские кавычки/тире, markdown-выделение (* и `). Многоточие в цитате (… или ...)
делит её на фрагменты, которые должны встретиться в тексте по порядку.

Запуск:  python3 evals/check_citations.py projects/<slug> [--n 20] [--seed 1] [--all-status] [--min 0.9]
Код выхода: 0 — ок; 1 — доля найденных ниже --min; 2 — ошибка входных данных.
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

_TRANS = str.maketrans({
    "ё": "е", "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "‘": "'", "’": "'",
    "—": "-", "–": "-", "‑": "-", " ": " ", "*": "", "`": "",
})
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.|\[\.\.\.\]|\[…\])\s*")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().translate(_TRANS)).strip()


def quote_in_text(quote: str, text: str) -> bool:
    parts = [norm(p) for p in _ELLIPSIS.split(quote) if norm(p)]
    if not parts:
        return False
    pos = 0
    for p in parts:
        i = text.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"предупреждение: {path.name}:{n} не JSON ({e})", file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Выборочная проверка цитат evidence против sources/")
    ap.add_argument("project", type=Path, help="путь к projects/<slug>")
    ap.add_argument("--n", type=int, default=20, help="размер выборки (по умолчанию 20)")
    ap.add_argument("--seed", type=int, default=1, help="seed выборки (по умолчанию 1)")
    ap.add_argument("--all-status", action="store_true", help="проверять и superseded/retracted")
    ap.add_argument("--min", type=float, default=None, help="порог доли найденных для кода выхода")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    a = ap.parse_args()

    ev_path = a.project / "evidence.jsonl"
    if not ev_path.is_file():
        print(f"ошибка: нет {ev_path}", file=sys.stderr)
        return 2
    facts = [e for e in load_jsonl(ev_path)
             if e.get("type") == "fact" and (a.all_status or e.get("status", "active") == "active")]
    if not facts:
        print("evidence типа fact не найдено — проверять нечего")
        return 0
    sample = random.Random(a.seed).sample(facts, min(a.n, len(facts)))

    cache: dict[str, str | None] = {}

    def source_text(sid: str) -> str | None:
        if sid not in cache:
            p = a.project / "sources" / f"{sid}.md"
            cache[sid] = norm(p.read_text(encoding="utf-8")) if p.is_file() else None
        return cache[sid]

    results = []
    for e in sorted(sample, key=lambda x: x.get("id", "")):
        quote, sids = (e.get("quote") or "").strip(), e.get("source_ids") or []
        if not quote:
            status, where = "no_quote", ""
        elif not sids:
            status, where = "no_source", ""
        else:
            status, where = "not_found", ""
            missing = []
            for sid in sids:
                txt = source_text(sid)
                if txt is None:
                    missing.append(sid)
                elif quote_in_text(quote, txt):
                    status, where = "found", sid
                    break
            if status != "found" and missing and len(missing) == len(sids):
                status, where = "source_file_missing", ",".join(missing)
        results.append({"id": e.get("id"), "status": status, "source": where or ",".join(sids),
                        "quote": quote[:80]})

    found = sum(r["status"] == "found" for r in results)
    share = found / len(results)
    if a.json:
        print(json.dumps({"project": str(a.project), "facts_total": len(facts), "checked": len(results),
                          "found": found, "share_found": round(share, 3), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"Проект: {a.project}")
        print(f"evidence типа fact: {len(facts)}; проверено (seed={a.seed}): {len(results)}")
        print(f"Цитата найдена в источнике: {found}/{len(results)} = {share:.0%}")
        bad = [r for r in results if r["status"] != "found"]
        if bad:
            print("Не подтверждено:")
            for r in bad:
                print(f"  {r['id']}  {r['status']:<20} {r['source']:<8} «{r['quote']}»")
    if a.min is not None and share < a.min:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
