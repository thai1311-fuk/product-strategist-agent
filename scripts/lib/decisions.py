"""Записи решений decisions/D###-slug.md и их плановый/событийный пересмотр."""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from . import store
from .store import SAError
from .validate import parse_frontmatter

DATE_TRIGGER_RE = re.compile(r"дата\s*:?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
STATUSES = ["active", "review_due", "revised", "closed"]
# даты в обязательствах: 2026-12-15 или 15.12.2026
ANY_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_dates(text: str) -> list[_dt.date]:
    out = []
    for m in ANY_DATE_RE.finditer(str(text)):
        try:
            if m.group(1):
                out.append(_dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            else:
                out.append(_dt.date(int(m.group(6)), int(m.group(5)), int(m.group(4))))
        except ValueError:
            continue
    return out


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def find_duplicate(pdir: Path, decision: str, based_on, key: str | None = None) -> Path | None:
    """Решение с тем же ключом или с той же формулировкой и теми же основаниями (кроме closed)."""
    based = sorted(store.split_ids(based_on))
    for f, meta, _ in load_all(pdir):
        if key and meta.get("key") == key:
            return f
        if (meta.get("status") != "closed" and _norm(meta.get("decision", "")) == _norm(decision)
                and sorted(meta.get("based_on") or []) == based):
            return f
    return None


def _slugify(text: str) -> str:
    tr = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                       "abvgdeejziiklmnoprstufhccss_y_eua")
    s = text.lower().translate(tr)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:40].rstrip("-")) or "decision"


def _fm_value(v) -> str:
    if isinstance(v, list):
        if all(isinstance(x, str) and store.ID_RE.match(x) for x in v):
            return "[" + ", ".join(v) + "]"
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_\-]+", v):
        return v
    return json.dumps(v, ensure_ascii=False)


def render(meta: dict, body: str) -> str:
    order = ["id", "date", "decision", "owner", "based_on", "metrics", "review_triggers", "commitments", "status"]
    keys = order + [k for k in meta if k not in order]
    head = "\n".join(f"{k}: {_fm_value(meta[k])}" for k in keys if k in meta)
    return f"---\n{head}\n---\n\n{body.lstrip()}"


def load_all(pdir: Path) -> list[tuple[Path, dict, str]]:
    out = []
    for f in sorted((pdir / "decisions").glob("D*.md")):
        meta, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        out.append((f, meta, body))
    return out


def add(pdir: Path, decision: str, owner: str, based_on: str | list[str], metrics: list[str],
        triggers: list[str], commitments: list[str], date: str | None = None,
        review_date: str | None = None, context: str = "", slug: str | None = None,
        key: str | None = None) -> Path:
    """Записать решение. Идемпотентно: то же key, либо та же формулировка с теми же основаниями → существующий файл."""
    dup = find_duplicate(pdir, decision, based_on, key)
    if dup:
        return dup
    based = store.split_ids(based_on)
    concl = store.by_id(store.read_jsonl(pdir / "conclusions.jsonl"))
    bad = [c for c in based if c not in concl]
    if not based or bad:
        raise SAError(f"based_on: укажите существующие выводы C### (не найдены: {', '.join(bad) or 'пусто'})")
    weak = [c for c in based if concl[c].get("status") not in ("active", "revised")]
    if weak:
        raise SAError(f"Основания {', '.join(weak)} не активны (needs_review/retracted) — сначала пересмотрите их.")
    if not metrics:
        raise SAError("Нужна хотя бы одна метрика: --metric \"…: базовое X → цель Y к дате\"")
    triggers = list(triggers or [])
    if review_date:
        try:
            _dt.date.fromisoformat(review_date)
        except ValueError:
            raise SAError(f"--review-date должна быть ГГГГ-ММ-ДД: «{review_date}»") from None
        if not any(review_date in DATE_TRIGGER_RE.findall(str(t)) for t in triggers):
            triggers.append(f"дата: {review_date}")
    if not triggers:
        raise SAError("Нужно условие пересмотра: --trigger \"если … то пересмотреть\" и/или --review-date ГГГГ-ММ-ДД")
    if not commitments:
        raise SAError("Нужны обязательства: --commitment \"кто что делает к какой дате\"")
    date = date or store.today().isoformat()
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        raise SAError(f"Дата решения должна быть в формате ГГГГ-ММ-ДД: «{date}»") from None
    existing = [m.get("id", "") for _, m, _ in load_all(pdir)]
    did = store.next_id([{"id": i} for i in existing], "D")
    meta = {"id": did, "date": date, "decision": decision, "owner": owner, "based_on": based,
            "metrics": metrics, "review_triggers": triggers, "commitments": commitments, "status": "active"}
    if key:
        meta["key"] = key
    body = (f"# {did}. {decision}\n\n"
            f"## Контекст и основания\n{context or 'Краткое обоснование со ссылками: ' + ', '.join(f'[{c}]' for c in based)}\n\n"
            "## Рассмотренные варианты\nсм. outputs/options.md\n\n"
            "## Риски и что будет сигналом пересмотра\nсм. review_triggers и outputs/test_plan.md\n\n"
            "## История пересмотров\n- " + f"{date}: решение принято\n")
    path = pdir / "decisions" / f"{did}-{_slugify(slug or decision)}.md"
    store.atomic_write_text(path, render(meta, body))
    store.log_action(pdir, "decision_add", did, detail=decision, actor="human")
    return path


def review(pdir: Path, apply: bool = True, horizon_days: int = 30) -> dict:
    """Что пора пересмотреть: дата триггера прошла или основания needs_review/retracted.

    Возвращает {"due": [(id, path, [причины])], "upcoming": [(id, дата, скоро?)]}. При apply статус меняется на review_due.
    """
    concl = store.by_id(store.read_jsonl(pdir / "conclusions.jsonl"))
    today = store.today()
    due, upcoming, overdue = [], [], []
    for f, meta, body in load_all(pdir):
        if meta.get("status") in ("closed", "revised"):
            continue
        reasons = []
        for c in meta.get("commitments") or []:
            ds = parse_dates(c)
            if ds and max(ds) < today:
                overdue.append((meta.get("id"), str(c), max(ds).isoformat()))
        for t in meta.get("review_triggers") or []:
            m = DATE_TRIGGER_RE.search(str(t))
            if not m:
                continue
            try:
                d = _dt.date.fromisoformat(m.group(1))
            except ValueError:
                reasons.append(f"некорректная дата в триггере: {t}")
                continue
            if d <= today:
                reasons.append(f"наступила дата пересмотра {d}")
            else:
                upcoming.append((meta.get("id"), d.isoformat(), (d - today).days <= horizon_days))
        for c in meta.get("based_on") or []:
            st = concl.get(c, {}).get("status")
            if st is None:
                reasons.append(f"основание {c} не найдено")
            elif st in ("needs_review", "retracted"):
                reasons.append(f"основание {c}: {st}")
        if not reasons and meta.get("status") == "review_due":
            reasons.append("статус review_due: пересмотрите и закройте — sa.py decision update <slug> "
                           f"{meta.get('id')} --status active|revised|closed [--review-date ГГГГ-ММ-ДД]")
        if reasons:
            due.append((meta.get("id"), f, reasons))
            if apply and meta.get("status") != "review_due":
                meta["status"] = "review_due"
                body = body.rstrip() + f"\n- {today.isoformat()}: отмечено к пересмотру — {'; '.join(reasons)}\n"
                store.atomic_write_text(f, render(meta, body))
                store.log_action(pdir, "decision_review_due", meta.get("id", f.name), detail="; ".join(reasons))
    upcoming.sort(key=lambda x: x[1])
    return {"due": due, "upcoming": upcoming, "overdue_commitments": overdue}


def update(pdir: Path, did: str, status: str | None = None, note: str = "", review_date: str | None = None) -> Path:
    """Закрыть пересмотр решения: статус active (в силе) | revised | closed и/или новая плановая дата пересмотра
    (заменяет прежние «дата: …» в review_triggers, не дублируя)."""
    if status is None and not review_date:
        raise SAError("Нужен --status и/или --review-date ГГГГ-ММ-ДД")
    if status is not None and status not in STATUSES:
        raise SAError(f"Недопустимый статус «{status}». Допустимо: {', '.join(STATUSES)}")
    if review_date:
        try:
            _dt.date.fromisoformat(review_date)
        except ValueError:
            raise SAError(f"--review-date должна быть ГГГГ-ММ-ДД: «{review_date}»") from None
    did = did.strip().upper()
    for f, meta, body in load_all(pdir):
        if meta.get("id") == did:
            line = f"\n- {store.today().isoformat()}:"
            if status:
                meta["status"] = status
                line += f" статус {status}"
            if review_date:
                trig = [t for t in (meta.get("review_triggers") or []) if not DATE_TRIGGER_RE.search(str(t))]
                trig.append(f"дата: {review_date}")
                meta["review_triggers"] = trig
                line += f"{',' if status else ''} следующий пересмотр {review_date}"
            body = body.rstrip() + line + (f" — {note}" if note else "") + "\n"
            store.atomic_write_text(f, render(meta, body))
            store.log_action(pdir, "decision_update", did, detail=f"{status or ''} {review_date or ''}; {note}".strip(),
                             actor="human")
            return f
    raise SAError(f"Решение {did} не найдено в decisions/.")
