#!/usr/bin/env python3
"""SessionStart: печатает сводку активного проекта (stdout попадает в контекст модели).

Активный проект — последний обновлённый state.json в projects/. Хук никогда не падает (exit 0).
Ручной эквивалент (Cursor/Codex): python3 scripts/sa.py resume
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> None:
    try:
        json.loads(sys.stdin.read() or "{}")  # вход не нужен, но читаем, чтобы не оставлять поток
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib import state, store  # noqa: PLC0415
        pdir = store.active_project()
        if pdir is None:
            print("[Стратег] Активного проекта нет. Начать: python3 scripts/sa.py new <slug> --title \"…\" "
                  "(этап 0 — уточнение запроса, см. AGENTS.md).")
            return
        others = [p.name for p in store.list_projects() if p != pdir]
        print("[Стратег] Сводка активного проекта (продолжайте с «Следующий шаг»):")
        print("\n".join(state.resume_lines(pdir, max_lines=40)))
        if others:
            print(f"Другие проекты: {', '.join(others[:5])} — если работаем над ними: sa.py resume <slug>")
    except Exception as exc:  # noqa: BLE001
        print(f"[Стратег] Не удалось собрать сводку ({type(exc).__name__}: {exc}). "
              "Выполните вручную: python3 scripts/sa.py resume")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
