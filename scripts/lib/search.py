"""Поиск по проекту (evidence/conclusions/sources/outputs/decisions) и по карточкам методов knowledge/methods.

Без индексов и моделей: слова запроса сопоставляются с текстом по началу слова (грубый «стемминг» по префиксу),
результат ранжируется по числу совпавших слов. Для найденных доказательств/выводов показываются связи
E → C → D (какие выводы на них опираются и какие решения на выводы).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import decisions, store

STOP = {"для", "что", "как", "это", "или", "при", "про", "над", "под", "его", "они", "все", "the", "and", "for"}
METHODS_DIR = store.ROOT / "knowledge" / "methods"


def _stem(w: str) -> str:
    return w[:5] if len(w) > 5 else w


def tokens(query: str) -> list[str]:
    ws = [w for w in re.findall(r"[\w%$€₽-]+", query.lower()) if len(w) >= 3 and w not in STOP]
    return list(dict.fromkeys(_stem(w) for w in ws))


def score(text: str, toks: list[str]) -> int:
    low = text.lower()
    return sum(1 for t in toks if re.search(r"(?<![\w])" + re.escape(t), low))


def _children(pdir: Path) -> tuple[dict, dict]:
    """(зависимые выводы по ID, решения по выводам)."""
    concl = store.read_jsonl(pdir / "conclusions.jsonl")
    dep: dict[str, list[str]] = {}
    for c in concl:
        for d in c.get("depends_on") or []:
            dep.setdefault(d, []).append(c["id"])
    dec: dict[str, list[str]] = {}
    for _f, meta, _b in decisions.load_all(pdir):
        for c in meta.get("based_on") or []:
            dec.setdefault(c, []).append(meta.get("id", "?"))
    return dep, dec


def _chain(item_id: str, dep: dict, dec: dict) -> str:
    seen, order, queue = {item_id}, [], [item_id]
    while queue:
        cur = queue.pop(0)
        for c in dep.get(cur, []):
            if c not in seen:
                seen.add(c)
                order.append(c)
                queue.append(c)
    ds = []
    for c in [item_id] + order:
        for d in dec.get(c, []):
            if d not in ds:
                ds.append(d)
    parts = []
    if order:
        parts.append("выводы: " + ", ".join(order))
    if ds:
        parts.append("решения: " + ", ".join(ds))
    return " → ".join(parts) if parts else "на него пока ничего не опирается"


def search_project(pdir: Path, query: str, limit: int = 12) -> list[str]:
    toks = tokens(query)
    if not toks:
        raise store.SAError("Запрос слишком короткий: нужны слова от 3 букв.")
    dep, dec = _children(pdir)
    hits: list[tuple[int, int, str]] = []  # (−score, порядок, строка)
    n = 0

    def add(sc: int, line: str) -> None:
        nonlocal n
        if sc:
            hits.append((-sc, n, line))
            n += 1

    for e in store.read_jsonl(pdir / "evidence.jsonl"):
        sc = score(f"{e.get('claim', '')} {e.get('quote', '')}", toks)
        st = e.get("status", "active")
        add(sc, f"{e['id']} [{e.get('type')}/{st}] {store.truncate(e.get('claim'), 110)} ← "
                f"{','.join(e.get('source_ids') or []) or '—'}\n      связи: {_chain(e['id'], dep, dec)}")
    ev = store.by_id(store.read_jsonl(pdir / "evidence.jsonl"))
    for c in store.read_jsonl(pdir / "conclusions.jsonl"):
        sc = score(c.get("statement", ""), toks)
        deps = ",".join(c.get("depends_on") or [])
        add(sc, f"{c['id']} [{c.get('kind')}/{c.get('status')}] {store.truncate(c.get('statement'), 110)} ← {deps}\n"
                f"      связи: {_chain(c['id'], dep, dec)}")
    for s in store.read_jsonl(pdir / "sources.jsonl"):
        sc = score(f"{s.get('title', '')} {s.get('notes', '')} {s.get('publisher', '')}", toks)
        add(sc, f"{s['id']} [источник/{s.get('kind')}] {store.truncate(s.get('title'), 100)} — "
                f"{store.truncate(s.get('locator'), 60)}")
    for f in sorted((pdir / "outputs").glob("*.md")):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            add(score(line, toks), f"outputs/{f.name}:{i}  {store.truncate(line.strip(), 120)}")
    for f, meta, _b in decisions.load_all(pdir):
        add(score(str(meta.get("decision", "")), toks), f"{meta.get('id')} [решение/{meta.get('status')}] "
                                                         f"{store.truncate(meta.get('decision'), 110)} ← "
                                                         f"{','.join(meta.get('based_on') or [])}")
    hits.sort()
    out = [f"Поиск «{query}» в {pdir.name} (слова: {', '.join(toks)}): найдено {len(hits)}"]
    out += [h[2] for h in hits[:limit]]
    if len(hits) > limit:
        out.append(f"… ещё {len(hits) - limit}; уточните запрос")
    if not hits:
        out.append("Ничего не найдено. Попробуйте другие слова или sa.py search --methods \"…\" по карточкам методов.")
    return out


def _cards() -> list[Path]:
    return sorted(METHODS_DIR.glob("*/*.md")) if METHODS_DIR.exists() else []


def search_methods(query: str, limit: int = 10) -> list[str]:
    toks = tokens(query)
    if not toks:
        raise store.SAError("Запрос слишком короткий: нужны слова от 3 букв.")
    hits = []
    for f in _cards():
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        title = lines[0].lstrip("# ").strip() if lines else f.stem
        sc = score(title, toks) * 3 + score(f.stem.replace("-", " "), toks) * 2
        best, best_sc = None, 0
        for i, line in enumerate(lines, 1):
            ls = score(line, toks)
            sc += ls
            if ls > best_sc:
                best, best_sc = (i, line), ls
        if sc:
            rel = f.relative_to(store.ROOT).as_posix()
            hits.append((-sc, rel, f"{rel}  {title}" + (f"\n      {best[0]}: {store.truncate(best[1].strip(), 110)}"
                                                       if best else "")))
    hits.sort()
    out = [f"Карточки методов по «{query}» (слова: {', '.join(toks)}): найдено {len(hits)}"]
    out += [h[2] for h in hits[:limit]]
    if not hits:
        out.append("Ничего не найдено. Оглавление: knowledge/INDEX.md")
    return out


def _summary(lines: list[str]) -> str:
    """Одна строка о карточке: первый пункт/фраза после «Когда применять»."""
    for i, line in enumerate(lines):
        low = line.lower()
        if "когда применять" in low:
            tail = re.sub(r"^.*?когда применять:?\**:?\s*", "", line, flags=re.I).strip(" -*")
            if len(tail) < 15:
                nxt = next((x for x in lines[i + 1:] if x.strip() and not x.startswith("#")), "")
                tail = nxt.strip(" -*")
            return re.sub(r"\*+", "", tail)
    return next((x.strip() for x in lines[1:] if x.strip() and not x.startswith("#")), "")


def build_index() -> str:
    out = ["# Оглавление карточек методов", "",
           "Читай нужную карточку перед этапом, а не все сразу. Поиск: `python3 scripts/sa.py search --methods \"запрос\"`.",
           "Файл собирается командой `python3 scripts/sa.py search --index`.", ""]
    names = {"product": "Продукт", "comms": "Коммуникации", "research": "Исследование и данные", "ux": "UX и интервью"}
    for d in sorted(METHODS_DIR.iterdir()):
        if not d.is_dir():
            continue
        out += [f"## {names.get(d.name, d.name)} (`methods/{d.name}/`)", ""]
        for f in sorted(d.glob("*.md")):
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            title = lines[0].lstrip("# ").strip() if lines else f.stem
            out.append(f"- [{title}](methods/{d.name}/{f.name}) — {store.truncate(_summary(lines), 150)}")
        out.append("")
    return "\n".join(out)
