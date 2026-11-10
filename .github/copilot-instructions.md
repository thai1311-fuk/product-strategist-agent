# Стратег Фабрики продуктов — инструкции для GitHub Copilot

Единый источник правил и процесса — `AGENTS.md` в корне репозитория (контракты данных — `docs/CONTRACT.md`). Прочитай его и следуй ему; здесь процесс не дублируется.

Хуков в Copilot нет, поэтому вручную (или через MCP-инструменты `sa_session_start` / `sa_session_end`):
- в начале работы: `python3 scripts/sa.py session start <slug>` (сводка и next_step);
- после каждого этапа: `python3 scripts/sa.py stage <slug> …`, перед ответом пользователю: `python3 scripts/sa.py validate <slug>`;
- в конце: `python3 scripts/sa.py session end <slug> --model <имя модели> --in N --out N --source estimate`.

Процедуры лежат в `.claude/skills/*/SKILL.md`, роли субагентов — в `.claude/agents/*.md`: это обычный markdown, открой нужный файл и выполни как отдельный шаг (см. `docs/PORTABILITY.md`).
MCP-сервер `strategist` описан в `.vscode/mcp.json` (запускает `python3 scripts/mcp_server.py`).
