#!/usr/bin/env python3
"""Stop: обновляет updated_at активного проекта и пишет расходы сессии в log/costs.jsonl.

Расходы считаются по transcript_path (сумма usage из ответов модели, с дедупликацией по message.id),
с ключом session_id: повторный вызов перезаписывает запись сессии, а не дублирует (см. cost.upsert_session).
Ничего не блокирует: всегда exit 0 без решения "block".
Ручной эквивалент: python3 scripts/sa.py cost add <slug> --model … --in N --out N
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "scripts"))


def usage_by_model(transcript: Path) -> dict:
    """{модель: {input_tokens, output_tokens, cache_write, cache_read}} по транскрипту Claude Code (jsonl)."""
    from lib import cost  # noqa: PLC0415
    routing = cost.load_routing()
    per_msg: dict = {}
    with transcript.open(encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            model = str(msg.get("model") or "")
            if not model or model.startswith("<"):  # <synthetic> и служебные записи
                continue
            # одна реплика может быть разбита на несколько строк с одинаковым id — берём последнюю
            per_msg[msg.get("id") or rec.get("uuid") or f"line{n}"] = (model, msg["usage"])
    out: dict = {}
    for model, u in per_msg.values():
        full = cost.resolve_model(model, routing) or model
        agg = out.setdefault(full, {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0})
        agg["input_tokens"] += int(u.get("input_tokens") or 0)
        agg["output_tokens"] += int(u.get("output_tokens") or 0)
        agg["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        agg["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
    return out


def main() -> None:
    data = json.loads(sys.stdin.read() or "{}")
    from lib import cost, state, store  # noqa: PLC0415
    pdir = store.active_project()
    if pdir is None:
        return
    state.touch(pdir)
    tp = data.get("transcript_path")
    session = str(data.get("session_id") or "")
    if not tp or not session or not Path(tp).expanduser().is_file():
        return
    per_model = usage_by_model(Path(tp).expanduser())
    if per_model:
        stage = state.load(pdir).get("stage", "intake")
        cost.upsert_session(pdir, session, stage, per_model)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — хук не должен мешать работе
        print(f"[Стратег] stop_state: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(0)
