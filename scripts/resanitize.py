#!/usr/bin/env python3
"""Повторное обезличивание сохранённых источников проекта.

    python3 scripts/resanitize.py <slug> [--dry-run]

Для каждого projects/<slug>/sources/S*.md заново прогоняет тело внутри <<<SOURCE …>>> … <<<END …>>>
через sanitize_text (новые правила ПДн и инъекций), снимает устаревшие пометки ⚠, обновляет flags/sha256
во frontmatter и в sources.jsonl, обезличивает цитаты в evidence.jsonl, пишет событие в журнал (sa.py log).
Нужен, когда правила в scripts/lib/sanitize.py изменились после того, как источники уже скачаны.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import sanitize, store  # noqa: E402

RX_BLOCK = re.compile(r"(<<<SOURCE (?P<sid>S\d+)[^\n]*>>>\n)(?P<body>[\s\S]*?)(\n<<<END (?P=sid)>>>)")
HEADER = re.compile(r"^⚠ В источнике найдены признаки prompt-injection[^\n]*\n\n?", re.M)
MARK = re.compile(re.escape(sanitize.INJECTION_MARK) + r" (?P<line>.*?) ⚠$", re.M)
SANITIZE_FLAGS = {"injection_suspected", "pii_suspected", "secret_suspected"}


def _unmark(body: str) -> str:
    body = HEADER.sub("", body)
    return MARK.sub(lambda m: m.group("line"), body)


def _update_frontmatter(text: str, flags: list[str], sha: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    head, rest = text[4:end], text[end:]
    out = []
    for line in head.split("\n"):
        if line.startswith("flags:"):
            line = "flags: " + json.dumps(flags, ensure_ascii=False)
        elif line.startswith("sha256:"):
            line = "sha256: " + json.dumps(sha)
        out.append(line)
    return "---\n" + "\n".join(out) + rest


def process(slug: str, dry: bool) -> int:
    pdir = store.project_dir(slug)
    total, changed_ids, new_flags = 0, [], {}
    for f in sorted((pdir / "sources").glob("S*.md")):
        text = f.read_text(encoding="utf-8")
        m = RX_BLOCK.search(text)
        if not m:
            continue
        sid, raw = m.group("sid"), _unmark(m.group("body"))
        before = raw.count(sanitize.AUTHOR_MASK) + raw.count("скрыт]")
        clean, flags = sanitize.sanitize_text(raw)
        n = clean.count(sanitize.AUTHOR_MASK) + clean.count("скрыт]") - before
        new_flags[sid] = set(flags)
        if clean.strip() == m.group("body").strip():
            continue
        total += max(n, 0)
        changed_ids.append(sid)
        print(f"{sid}: замен ПДн {max(n, 0)}; флаги {', '.join(flags) or '—'}")
        if not dry:
            sha = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
            new_text = text[:m.start("body")] + clean.strip() + text[m.end("body"):]
            fm_flags = sorted((set(_fm_flags(text)) - SANITIZE_FLAGS) | set(flags))
            store.atomic_write_text(f, _update_frontmatter(new_text, fm_flags, sha))

    # реестр источников: пересчитываем только флаги, которые ставит sanitize
    spath = pdir / "sources.jsonl"
    q_changed = 0
    if not dry:
        with store.locked(spath):
            rows = store.read_jsonl(spath)
            for r in rows:
                if r.get("id") in new_flags:
                    r["flags"] = sorted((set(r.get("flags") or []) - SANITIZE_FLAGS) | new_flags[r["id"]])
            store.write_jsonl(spath, rows)
        epath = pdir / "evidence.jsonl"
        if epath.exists():
            with store.locked(epath):
                rows = store.read_jsonl(epath)
                for r in rows:
                    q, k = sanitize.anonymize(r.get("quote") or "")
                    c, k2 = sanitize.anonymize(r.get("claim") or "")
                    if k or k2:
                        r["quote"], r["claim"] = q, c
                        q_changed += 1
                store.write_jsonl(epath, rows)
    print(f"Итого: источников изменено {len(changed_ids)}, замен ПДн {total}, цитат обезличено {q_changed}"
          + (" (пробный прогон, файлы не изменены)" if dry else ""))
    if not dry:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), "log", slug, "--action", "resanitize",
                        "--target", ",".join(changed_ids) or "—"], check=False, capture_output=True)
    return 0


def _fm_flags(text: str) -> list[str]:
    m = re.search(r"^flags: (.*)$", text[:3000], re.M)
    try:
        return json.loads(m.group(1)) if m else []
    except ValueError:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Повторное обезличивание источников проекта")
    ap.add_argument("slug")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что изменится")
    a = ap.parse_args()
    try:
        return process(a.slug, a.dry_run)
    except store.SAError as exc:  # type: ignore[attr-defined]
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
