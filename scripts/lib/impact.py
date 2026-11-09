"""Анализ влияния: что затронет изменение источника, доказательства или вывода.

Обход транзитивный: источник → доказательства из него → выводы, опирающиеся на них →
выводы, опирающиеся на эти выводы … Затронутые активные выводы получают статус needs_review,
а в outputs/*.md и decisions/*.md находятся строки, которые надо перечитать.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import store
from .store import SAError


def affected(pdir: Path, ids: list[str]) -> tuple[list[str], list[str], dict[str, str]]:
    """Возвращает (затронутые доказательства, затронутые выводы по порядку обхода, причина для каждого)."""
    sources = store.by_id(store.read_jsonl(pdir / "sources.jsonl"))
    evidence = store.read_jsonl(pdir / "evidence.jsonl")
    conclusions = store.read_jsonl(pdir / "conclusions.jsonl")
    ev_ids, c_ids = {e["id"] for e in evidence}, {c["id"] for c in conclusions}
    unknown = [i for i in ids if i not in sources and i not in ev_ids and i not in c_ids]
    if unknown:
        raise SAError(f"Не найдены: {', '.join(unknown)}")

    why: dict[str, str] = {}
    hit_e: list[str] = []
    for i in ids:
        if i.startswith("E"):
            hit_e.append(i)
            why[i] = "изменено напрямую"
    for e in evidence:  # доказательства из затронутых источников
        srcs = [s for s in e.get("source_ids", []) if s in ids]
        if srcs and e["id"] not in hit_e:
            hit_e.append(e["id"])
            why[e["id"]] = f"основано на {', '.join(srcs)}"

    hit = set(hit_e) | {i for i in ids if i.startswith(("C", "S"))}
    hit_c: list[str] = []
    changed = True
    while changed:  # до неподвижной точки (циклы не страшны: каждый вывод добавляется один раз)
        changed = False
        for c in conclusions:
            if c["id"] in hit_c or c["id"] in ids:
                continue
            via = [d for d in c.get("depends_on", []) if d in hit]
            if via:
                hit_c.append(c["id"])
                hit.add(c["id"])
                why[c["id"]] = f"зависит от {', '.join(via)}"
                changed = True
    return hit_e, hit_c, why


def find_mentions(pdir: Path, ids: set[str]) -> list[tuple[str, int, str, list[str]]]:
    """Строки в outputs/*.md и decisions/*.md, где упомянуты затронутые ID."""
    if not ids:
        return []
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, ids))) + r")\b")
    hits = []
    files = sorted((pdir / "outputs").glob("*.md")) + sorted((pdir / "decisions").glob("*.md"))
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            found = sorted(set(pat.findall(line)))
            if found:
                hits.append((str(f.relative_to(pdir)), n, store.truncate(line, 140), found))
    return hits


def run(pdir: Path, ids: list[str], mark: bool = True, quiet: bool = False) -> dict:
    ids = store.split_ids(ids)
    if not ids:
        raise SAError("Укажите ID: источник S###, доказательство E### или вывод C###.")
    hit_e, hit_c, why = affected(pdir, ids)

    marked: list[str] = []
    if mark and hit_c:
        path = pdir / "conclusions.jsonl"
        with store.locked(path):
            rows = store.read_jsonl(path)
            for r in rows:
                if r["id"] in hit_c and r.get("status") in ("active", "revised"):
                    r["status"] = "needs_review"
                    r["note"] = f"impact {','.join(ids)}: {why[r['id']]}"
                    r["updated_at"] = store.now_iso()
                    marked.append(r["id"])
            store.write_jsonl(path, rows)

    mentions = find_mentions(pdir, set(ids) | set(hit_e) | set(hit_c))
    files = sorted({m[0] for m in mentions})
    store.log_action(pdir, "impact", ",".join(ids),
                     detail=f"доказательств {len(hit_e)}, выводов {len(hit_c)}, помечено {len(marked)}, файлов {len(files)}")
    if marked:
        from . import state  # noqa: PLC0415
        with store.locked(pdir / "state.json"):
            st = state.load(pdir)
            state.add_history(st, f"impact {','.join(ids)}: на пересмотр {', '.join(marked)}")
            state.save(pdir, st)

    if not quiet:
        print(f"Влияние изменения {', '.join(ids)}:")
        print(f"  Доказательства ({len(hit_e)}): " + (", ".join(f"{e} ({why[e]})" for e in hit_e) or "—"))
        print(f"  Выводы ({len(hit_c)}): " + (", ".join(f"{c} ({why[c]})" for c in hit_c) or "—"))
        if marked:
            print(f"  Помечены needs_review: {', '.join(marked)}")
        if mentions:
            print(f"  Пересмотреть в файлах ({len(files)}):")
            for f, n, line, found in mentions[:60]:
                print(f"    {f}:{n} [{', '.join(found)}] {line}")
            if len(mentions) > 60:
                print(f"    … ещё {len(mentions) - 60} строк")
        else:
            print("  Упоминаний в outputs/ и decisions/ нет.")
        if marked:
            print("Дальше: перечитайте выводы и закройте пересмотр: "
                  "sa.py conclusion update <slug> C### --status active|revised|retracted --note \"…\"; "
                  "затем sa.py decision review <slug>.")
    return {"evidence": hit_e, "conclusions": hit_c, "marked": marked, "mentions": mentions}
