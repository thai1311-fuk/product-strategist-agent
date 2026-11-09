#!/usr/bin/env python3
"""PostToolUse: компактная запись в log/actions.jsonl проекта (инструмент, цель, успех).

Содержимое файлов и ответы инструментов не пишутся; секреты маскируются; длинные значения обрезаются.
Вызовы самого sa.py пропускаются — CLI журналирует свои действия сам. Никогда не падает (exit 0).
Ручной эквивалент: python3 scripts/sa.py log <slug> --action … --target …
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "scripts"))

SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9]{20,}|xox[abp]-[a-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|(?:api[_-]?key|token|secret|password|passwd|authorization)\s*[=:]\s*(?:bearer\s+)?\S+|bearer\s+\S+)")
PROJ_RE = re.compile(r"projects/([a-z0-9][a-z0-9-]{0,63})(?:/|\b)")


def mask(text: str) -> str:
    text = SECRET_RE.sub("[скрыто]", text)
    try:
        from lib import sanitize  # noqa: PLC0415
        if hasattr(sanitize, "scan_secrets") and sanitize.scan_secrets(text):
            return "[скрыто: найден секрет]"
    except Exception:  # noqa: BLE001
        pass
    return text


def target_of(tool: str, ti: dict) -> str:
    for k in ("file_path", "notebook_path", "path", "url", "command", "query", "pattern", "description",
              "subagent_type", "prompt"):
        v = ti.get(k)
        if v:
            v = str(v)
            if k in ("file_path", "notebook_path", "path"):
                try:
                    v = str(Path(v).resolve().relative_to(ROOT))
                except Exception:  # noqa: BLE001
                    pass
            if k == "prompt":
                v = (ti.get("subagent_type") or "agent") + ": " + v
            return v
    return tool


def ok_of(resp) -> bool:
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error") or resp.get("success") is False or resp.get("interrupted"):
            return False
        code = resp.get("exit_code", resp.get("returncode"))
        if isinstance(code, int) and code != 0:
            return False
    return True


def main() -> None:
    data = json.loads(sys.stdin.read() or "{}")
    from lib import store  # noqa: PLC0415
    tool = str(data.get("tool_name") or "?")
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    raw_target = target_of(tool, ti)
    if tool == "Bash" and "scripts/sa.py" in raw_target:
        return
    m = PROJ_RE.search(raw_target)
    pdir = None
    if m and (store.projects_dir() / m.group(1) / "state.json").exists():
        pdir = store.projects_dir() / m.group(1)
    if pdir is None:
        pdir = store.active_project()
    if pdir is None:
        return
    session = str(data.get("session_id") or "claude-code")
    tuid = data.get("tool_use_id") or ""
    store.log_action(pdir, tool.lower(), store.truncate(mask(raw_target), 160), ok=ok_of(data.get("tool_response")),
                     actor="claude-code", session=session,
                     key=f"{session}:{tuid}" if tuid else None)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — журнал не должен мешать работе
        pass
    sys.exit(0)
