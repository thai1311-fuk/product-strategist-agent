# Переносимость: работа в любой среде и с любой моделью

Задание: проект переносится в другую среду разработки и продолжается там. Здесь — что для этого есть, что в какой среде подтверждено по документации и чего не хватает. Дата проверки документации: 2026-11-10. Форматы сред меняются, поэтому у каждого — ссылка.

## Принцип
Вся логика лежит в `AGENTS.md` (правила и процесс), `docs/CONTRACT.md` (форматы данных) и Python-ядре `scripts/sa.py`. Состояние — файлы в `projects/<slug>/`. Среда нужна только для того, чтобы модель читала `AGENTS.md` и вызывала команды `sa.py` (или MCP-инструменты `sa_*`, это те же команды). Конфиги сред — по 5–15 строк со ссылкой на `AGENTS.md`, процесс в них не дублируется.

## Таблица «среда → что подключено»
| Среда | Инструкции | Процедуры (skills) | Субагенты | MCP | Замена хуков | Расходы |
|---|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` → `AGENTS.md` | `.claude/skills` нативно | `.claude/agents` нативно | `.mcp.json` | хуки `.claude/hooks` | автоматически по транскрипту (хук Stop) |
| Cursor | `.cursor/rules/strategist.mdc` → `AGENTS.md` | читать `.claude/skills/*/SKILL.md` по просьбе | отдельный чат (см. ниже) | `.cursor/mcp.json` | `session start/end` | вручную/оценка |
| GitHub Copilot (VS Code) | `.github/copilot-instructions.md`; `AGENTS.md` VS Code читает и сам | как у Cursor | отдельный чат | `.vscode/mcp.json` | `session start/end` | вручную/оценка |
| Windsurf | `.windsurf/rules/strategist.md`; `AGENTS.md` читается как always-on | как у Cursor | отдельный чат/сессия | см. ниже (формат не подтверждён) | `session start/end` | вручную/оценка |
| OpenCode | `AGENTS.md` читает сам | как у Cursor | свой механизм агентов OpenCode не подключён; отдельная сессия | `opencode.json` | `session start/end` | вручную/оценка |
| Codex CLI | `AGENTS.md` читает сам | как у Cursor | отдельная сессия | `.codex/config.toml` (проектный, доверенные проекты) или `~/.codex/config.toml` | `session start/end` | вручную/оценка |
| Gemini CLI | `GEMINI.md` (импорт `@./AGENTS.md`) | как у Cursor | отдельная сессия | `.gemini/settings.json` | `session start/end` | вручную/оценка |
| GigaCode, Кодик, прочие | `AGENTS.md` целиком вставить/приложить в системный промпт, если среда не читает его сама | как у Cursor | отдельный чат | если среда поддерживает MCP (stdio) — `python3 scripts/mcp_server.py`, иначе только CLI | `session start/end` | вручную/оценка |

«Как у Cursor» для процедур: скажите модели «выполни процедуру из `.claude/skills/research/SKILL.md`» — это обычный markdown с шагами. «Вручную/оценка»: в конце сессии `session end … --source estimate` с числами из счётчика среды/провайдера (`--source provider`, если число взято из счётчика провайдера) или с грубой оценкой.

## Подтверждённые форматы (по официальной документации)
- **GitHub Copilot / VS Code.** Подтверждено: `.github/copilot-instructions.md` и `AGENTS.md` в корне репозитория читаются как инструкции проекта; MCP — `.vscode/mcp.json` с ключом верхнего уровня `servers` (не `mcpServers`), для stdio обязательны `type` и `command`, опциональны `args`, `cwd`, `env`. Ссылки: https://code.visualstudio.com/docs/copilot/customization/custom-instructions , https://code.visualstudio.com/docs/agents/reference/mcp-configuration . Страница MCP помечает `.vscode/mcp.json` как «deprecated in the guided flow», но доступным для совместимости; если редактор предлагает свой способ добавления сервера — используйте его с теми же command/args.
- **Windsurf.** Подтверждено: `AGENTS.md` читается как правило always-on; правила проекта в Markdown с frontmatter `trigger: always_on|glob|model_decision|manual`; актуальный путь `.devin/rules/*.md`, старый `.windsurf/rules/*.md` по-прежнему загружается (в репозитории использован старый путь, он совместим; для нового пути достаточно перенести файл). Документация переехала на docs.devin.ai: https://docs.devin.ai/desktop/cascade/memories . MCP: документация называет проектный файл `.devin/mcp_config.json`, но формат JSON на странице не приведён (https://docs.devin.ai/desktop/cascade/mcp). Поэтому MCP-файл Windsurf в репозиторий не добавлен. Практически: добавьте сервер через интерфейс MCP в Cascade с командой `python3` и аргументом `scripts/mcp_server.py` (рабочая папка — корень проекта) либо перенесите блок `mcpServers` из `.gemini/settings.json` и проверьте, что сервер увиделся. Без MCP всё работает через CLI.
- **OpenCode.** Подтверждено по самой JSON-схеме https://opencode.ai/config.json и странице https://opencode.ai/docs/mcp-servers/ : локальный сервер описывается как `mcp.servers.<имя>` с `type: "local"` и `command` — массивом (исполняемый файл и аргументы). Старая плоская форма `mcp.<имя>` помечена в схеме как deprecated. `AGENTS.md` OpenCode загружает как инструкции (https://opencode.ai/docs/config/).
- **Codex CLI.** Подтверждено: MCP — таблицы `[mcp_servers.<имя>]` с `command` (обязательно), `args`, `env`, `cwd`; сказано, что серверы можно ограничить проектом через `.codex/config.toml` только для доверенных проектов (https://learn.chatgpt.com/docs/extend/mcp?surface=cli — бывший developers.openai.com/codex/mcp). `AGENTS.md` читается от корня проекта вниз до рабочей папки, лимит 32 КиБ суммарно (https://learn.chatgpt.com/docs/agent-configuration/agents-md). Страница про AGENTS.md проектный `.codex/config.toml` не описывает; если он не подхватится — тот же блок в `~/.codex/config.toml`. Относительный путь `scripts/mcp_server.py` рассчитан на запуск из корня проекта; при сомнениях укажите `cwd`.
- **Gemini CLI.** Подтверждено: `mcpServers` в `.gemini/settings.json` (проектный) или `~/.gemini/settings.json`, поля `command`, `args`, `env`, `cwd` (https://geminicli.com/docs/tools/mcp-server/). `GEMINI.md` поддерживает импорт `@./файл.md`; имя файла контекста меняется настройкой `context.fileName` (в примере документации есть `AGENTS.md`) (https://geminicli.com/docs/cli/gemini-md/). Страницы документации сообщают, что с 2026-06-18 Gemini CLI заменяется на Antigravity CLI для бесплатных пользователей и Google One; формат Antigravity CLI не проверялся.
- **GigaCode (Сбер) и Кодик.** Не подтверждено. Официальной документации GigaCode о поддержке MCP или файла правил проекта найти не удалось (поиск 2026-11-10; найдены только MCP-сервер документации Sber API и комментарий, где пользователь выясняет, есть ли MCP для GigaCode/GigaIDE). Для Кодика документацию вообще не проверяли. Честный общий путь: передать модели `AGENTS.md` (вставить в системный промпт/первое сообщение, если среда не подхватывает его сама), а команды `python3 scripts/sa.py …` выполнять в терминале среды или руками, вставляя вывод в чат. MCP — только если в среде есть поддержка stdio-серверов; конфиг подбирается по её документации (команда `python3`, аргумент `scripts/mcp_server.py`).
- **Cursor и Claude Code** — уже настроены ранее (`.cursor/mcp.json`, `.mcp.json`); в этой проверке не пересматривались.

## Замена хукам: `session start` / `session end`
В Claude Code хуки сами подставляют сводку при старте и пишут расходы при остановке. В остальных средах:
```
python3 scripts/sa.py session start [slug]        # = resume + запись session_start в журнал; actor из SA_ACTOR, иначе "agent"
python3 scripts/sa.py session end [slug] --model <имя> --in N --out N [--cache-read N] [--cache-write N] \
        [--source estimate|provider] [--tier cheap|medium|strong] [--stage …]
                                                  # = обновить updated_at + записать session_end + расход (если переданы)
```
Те же операции в MCP: `sa_session_start`, `sa_session_end`. Без `slug` берётся активный проект. Без `--model` `session end` только фиксирует конец и обновляет `updated_at`. `--source estimate` — оценка, `provider` — число из счётчика провайдера (в отчёте видно, откуда цифра). Проверка опасных действий, которую в Claude Code делают хуки (запись в чужие проекты, секреты), в других средах не автоматическая: остаются `sa.py validate` и `python3 scripts/sa.py doctor`.

## Модели: уровни вместо имён
Основная абстракция — три уровня: `cheap`, `medium`, `strong` (`config/routing.json` → `tiers`, `stage_tiers`, `subagent_tiers`). Конкретную модель выбирают по `providers.<провайдер>.<уровень>`: приведены Anthropic, GigaChat (Lite/Pro/Max), YandexGPT (Lite/Pro; старшего уровня нет — для `strong` Pro или модель другого провайдера), OpenAI, Google Gemini, Qwen. Названия моделей — ориентиры, проверьте у провайдера. Цены у всех, кроме Anthropic, пустые (`null`): заполните по прайсу провайдера на дату использования. Цены Anthropic условные.

Учёт расходов не привязан к Claude: `cost add` и `session end` принимают любое имя модели.
- модель в `routing.json` (или в `providers` с заполненными ценами) — считается по её ценам;
- неизвестная модель и указан `--tier` — по ценам уровня (цены уровня сейчас = цены Anthropic того же уровня, это условность);
- неизвестная модель без `--tier` — расход записывается как $0 с предупреждением (его видно в выводе и в `note` записи), чтобы не терять факт вызова.
Эскалация («повторить на уровне выше») работает по уровням: cheap → medium → strong; `sa.py route` печатает уровень и напоминает взять модель этого уровня из `providers`.

## Субагенты в средах без субагентов
Файлы `.claude/agents/*.md` (collector, synthetic-respondent, consistency-checker, critic) — обычные markdown-промпты: роль, вход, процедура, формат ответа. В Claude Code их запускают как субагентов с узким набором инструментов и своей моделью. В другой среде:
1. Новый чат (или новая сессия CLI) на модели нужного уровня (`subagent_tiers`).
2. Первым сообщением — содержимое нужного `.claude/agents/<роль>.md` (кроме frontmatter) и вход: `slug` и вопрос. Имена инструментов `mcp__strategist__sa_*` — это MCP-инструменты `sa_*` или одноимённые команды `sa.py`.
3. Результат этот чат пишет в проект командами `sa.py` (evidence add и т. п.) — основной чат читает его оттуда, а не из пересказа.
4. Критика (critic) и проверка согласованности — только в чистом чате без истории основной работы: смысл в независимости.
Ограничения: нет принудительного ограничения инструментов (роль «не делает выводов» держится только на тексте промпта); нет параллельного запуска и автоматического возврата результата — всё руками пользователя; независимость критика обеспечивается дисциплиной (новый чат), а не средой.

## Что переносится без потерь, а что нет
Переносится: правила и процесс (`AGENTS.md`), все данные проекта, проверки (`validate`, `impact`, тесты), MCP-инструменты, журнал действий.
Не переносится автоматически: хуки (заменены командами `session`), автоматический учёт токенов по транскрипту, ограничение инструментов субагентов, нативные скиллы/субагенты Claude Code (заменены чтением markdown), проверка «PreToolUse» опасных действий. Качество результата зависит от модели: слабая модель хуже держит правила про цитаты и источники-как-данные, а `validate` этого не заменяет полностью.

## Эмуляция среды без Claude Code (не прогон в реальной IDE)
2026-11-10: репозиторий скопирован во временную папку без `.claude/`, `CLAUDE.md`, `.mcp.json`, `.git` и без `projects/` (rsync); `SA_ACTOR=emu-cli`. Только CLI:
`new emu` → `session start emu` (печатает сводку, пишет session_start) → `source add --kind user --file note.txt` (S001) → `evidence add --type fact --quote …` (E001) → `conclusion add` (C001) → `validate` (ошибок 0, 6 шаблонов пропущено) → `session end --model GigaChat-2-Pro --tier medium --in 12000 --out 3000` (расход $0.0810 по цене уровня medium) → `session end --model claude-haiku-5-5 --source provider …` ($0.0090) → `cost report` (две модели в одном отчёте). Замечание: `session start` для несуществующего проекта, как и `resume`, выдаёт ошибку — сначала `new`.
MCP по stdio: `initialize` → `serverInfo strategist` → `tools/list` (26 инструментов, среди них `sa_session_start`, `sa_session_end`) → `tools/call sa_session_start` для `emu` без ошибки.
Это эмуляция: сервер и CLI запускались прямыми вызовами, а не из Copilot, Windsurf, OpenCode, Codex или Gemini CLI. Корректность самих конфигов этих сред проверена только чтением документации и разбором файлов (JSON/TOML корректны, см. `tests/test_portability.py`), реальный запуск в этих средах не выполнялся.
