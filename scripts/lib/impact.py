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


def _decision_hits(pdir: Path, concl_ids: set[str]) -> list[tuple[str, list[str]]]:
    from .decisions import load_all  # noqa: PLC0415
    out = []
    for _f, meta, _b in load_all(pdir):
        hit = [c for c in (meta.get("based_on") or []) if c in concl_ids]
        if hit:
            out.append((str(meta.get("id")), hit))
    return out


def run(pdir: Path, ids: list[str], mark: bool = True, quiet: bool = False, full: bool = False,
        wave: str | None = None) -> dict:
    """Помечает затронутые выводы needs_review и печатает сводку.

    wave — время начала волны пересмотра (ISO). Выводы, у которых reviewed_at ≥ wave, уже пересмотрены
    в этой волне и повторно не помечаются (иначе каскад от conclusion update зацикливает пересмотр).
    """
    ids = store.split_ids(ids)
    if not ids:
        raise SAError("Укажите ID: источник S###, доказательство E### или вывод C###.")
    hit_e, hit_c, why = affected(pdir, ids)
    wave = wave or store.now_precise()
    wave_ts = store.parse_ts(wave)

    marked: list[str] = []
    skipped: list[str] = []
    if mark and hit_c:
        path = pdir / "conclusions.jsonl"
        with store.locked(path):
            rows = store.read_jsonl(path)
            for r in rows:
                if r["id"] not in hit_c or r.get("status") not in ("active", "revised"):
                    continue
                rev = store.parse_ts(r.get("reviewed_at"))
                if rev and wave_ts and rev >= wave_ts:
                    skipped.append(r["id"])
                    continue
                r["status"] = "needs_review"
                r["note"] = f"impact {','.join(ids)}: {why[r['id']]}"
                r["review_wave"] = wave
                r["updated_at"] = store.now_iso()
                marked.append(r["id"])
            store.write_jsonl(path, rows)

    mentions = find_mentions(pdir, set(ids) | set(hit_e) | set(hit_c))
    files = sorted({m[0] for m in mentions})
    dec = _decision_hits(pdir, set(hit_c) | {i for i in ids if i.startswith("C")})
    store.log_action(pdir, "impact", ",".join(ids),
                     detail=f"доказательств {len(hit_e)}, выводов {len(hit_c)}, помечено {len(marked)}, "
                            f"файлов {len(files)}, решений {len(dec)}")
    if marked:
        from . import state  # noqa: PLC0415
        with store.locked(pdir / "state.json"):
            st = state.load(pdir)
            state.add_history(st, f"impact {','.join(ids)}: на пересмотр {', '.join(marked)}")
            state.save(pdir, st)

    if not quiet:
        _print(pdir, ids, hit_e, hit_c, why, marked, skipped, mentions, dec, full)
    return {"evidence": hit_e, "conclusions": hit_c, "marked": marked, "skipped": skipped,
            "mentions": mentions, "decisions": dec}


def _print(pdir, ids, hit_e, hit_c, why, marked, skipped, mentions, dec, full) -> None:
    C = store.by_id(store.read_jsonl(pdir / "conclusions.jsonl"))
    slug = pdir.name
    print(f"Влияние изменения {', '.join(ids)}:")
    print(f"  Доказательства ({len(hit_e)}): " + (", ".join(hit_e[:25]) + (" …" if len(hit_e) > 25 else "") or "—"))
    print(f"  Выводы ({len(hit_c)}; помечено needs_review {len(marked)}" +
          (f"; уже пересмотрены в этой волне: {', '.join(skipped)}" if skipped else "") + "):")
    lim = len(hit_c) if full else 12
    for c in hit_c[:lim]:
        print(f"    {c} {store.truncate(C.get(c, {}).get('statement', ''), 60)}")
    if len(hit_c) > lim:
        print(f"    … ещё {len(hit_c) - lim}")
    if mentions:
        per_file: dict[str, int] = {}
        for f, *_ in mentions:
            per_file[f] = per_file.get(f, 0) + 1
        docs = [f for f in per_file if not f.startswith("decisions/")]
        print("  Документы: " + ("; ".join(f"{f} — {per_file[f]} мест" for f in docs[:10]) or "—"))
    else:
        print("  Документы: упоминаний в outputs/ и decisions/ нет")
    if dec:
        print("  Решения: " + "; ".join(f"{d} (основания {', '.join(c)})" for d, c in dec[:8]))
    if full and mentions:
        print("  Строки для пересмотра:")
        for f, n, line, found in mentions:
            print(f"    {f}:{n} [{', '.join(found)}] {line}")
    elif mentions:
        print(f"  Строки файл:строка — python3 scripts/sa.py impact {slug} {' '.join(ids)} --full")
    if marked:
        print(f"Дальше: порядок пересмотра — sa.py conclusion review-order {slug}; закрыть — "
              f"sa.py conclusion update {slug} C### --status active|revised|retracted --note \"…\"; "
              f"затем sa.py decision review {slug}.")


def review_order(pdir: Path) -> tuple[list[dict], list[dict]]:
    """Выводы needs_review в порядке зависимостей: сначала те, чьи зависимости уже не needs_review.
    Возвращает (упорядоченные, оставшиеся в цикле)."""
    rows = store.read_jsonl(pdir / "conclusions.jsonl")
    pending = {r["id"]: r for r in rows if r.get("status") == "needs_review"}
    order: list[dict] = []
    done: set[str] = set()
    while True:
        ready = [cid for cid, r in pending.items() if cid not in done
                 and all(d not in pending or d in done for d in r.get("depends_on", []))]
        if not ready:
            break
        for cid in sorted(ready):
            order.append(pending[cid])
            done.add(cid)
    cyclic = [r for cid, r in pending.items() if cid not in done]
    return order, cyclic
