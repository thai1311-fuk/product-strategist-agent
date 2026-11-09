# Контракт команды (единый для всех ролей)

Этот файл — источник истины для форматов данных, команд и владения файлами.
Меняет его только архитектор. Если тебе нужно изменение — напиши об этом в итоговом отчёте, не правь сам.

## Общие принципы
- Python 3.10+ **только стандартная библиотека** (никаких pip-зависимостей). Переносимость важнее удобства.
- Все тексты для пользователя — на русском. Код, ключи JSON, имена файлов — на английском.
- Одна точка входа CLI: `python3 scripts/sa.py <команда> ...` (sa = strategist agent).
- Агент работает в Claude Code **и** в Cursor/Codex. Поэтому вся логика — в `AGENTS.md` + Python-скриптах; хуки Claude Code только дополняют (автоматизируют то, что в Cursor делается командой вручную).
- Каждый компонент должен оправдывать свою сложность. Не плоди лишнего.

## Структура проекта задачи `projects/<slug>/`
```
brief.md            запрос, цель, получатель, ограничения, доступные данные (шаблон templates/brief.md)
state.json          состояние работы (см. ниже)
sources.jsonl       реестр источников
sources/            тексты источников: S001.md, S002.md … (frontmatter + очищенный текст)
evidence.jsonl      реестр доказательств (утверждения из источников)
conclusions.jsonl   выводы, зависящие от доказательств/других выводов
outputs/            situation.md, research_plan.md, options.md, product_strategy.md,
                    comms_strategy.md, test_plan.md, report.html
decisions/          D001-<slug>.md — записи решений
interviews/         guide.md, transcripts/ (обезличенные), synthetic/ (метка «СИНТЕТИКА»),
                    consent_log.md (псевдоним, дата, факт согласия — без ПДн), codebook.md
sources/screens/    скриншоты и изображения (source kind=image)
data/               таблицы (csv) и результаты анализа
log/actions.jsonl   журнал действий
log/costs.jsonl     журнал расходов
```

### state.json
```json
{"project":"slug","title":"…","stage":"intake","stage_status":"in_progress",
 "next_step":"…","open_questions":["…"],
 "gates":{"plan_approved":false,"option_chosen":null,"strategy_approved":false},
 "budget_usd":5.0,"created_at":"ISO","updated_at":"ISO",
 "history":[{"ts":"ISO","stage":"intake","event":"…"}]}
```
stage ∈ `intake, plan, collect, synthesis, options, strategy, test_plan, decision, monitor` — номера этапов 0–8 в этом порядке (в сообщениях пользователю: «Этап 2/8»).
stage_status ∈ `in_progress, awaiting_human, done`.

### sources.jsonl (одна строка — один источник)
```json
{"id":"S001","title":"…","locator":"https://… или путь","kind":"web|doc|table|interview|synthetic|image|user",
 "publisher":"…","retrieved_at":"ISO","reliability":"high|medium|low","allowed":true,
 "flags":["injection_suspected"],"notes":"…"}
```
### evidence.jsonl
```json
{"id":"E001","claim":"…","type":"fact|estimate|hypothesis|forecast|assumption|synthetic",
 "source_ids":["S001"],"quote":"дословная цитата ≤300 симв.","locator":"стр./раздел/якорь",
 "data_date":"2025 или ISO","confidence":"high|medium|low",
 "status":"active|superseded|retracted","superseded_by":null,"reason":null,
 "added_at":"ISO","added_by":"collector|analyst|human|…"}
```
Правила: `fact` обязан иметь source_ids и quote. `synthetic` (ответы ИИ-респондентов) никогда не доказывает спрос/распространённость. `hypothesis/forecast/assumption` могут быть без источника, но обязаны быть так помечены.

### conclusions.jsonl
```json
{"id":"C001","statement":"…","depends_on":["E001","C002"],"kind":"insight|option|recommendation|risk",
 "status":"active|needs_review|revised|retracted","updated_at":"ISO","note":null}
```
### Цитирование в outputs/*.md
Маркеры в квадратных скобках: `[E012]`, `[C003]`, `[S004]`. Гипотезы помечаются словом **Гипотеза:** или маркером на evidence типа hypothesis. Валидатор проверяет: маркер существует, статус active, числа в тексте сопровождаются маркером в том же абзаце.

### decisions/D001-slug.md (frontmatter)
```
---
id: D001
date: 2026-11-20
decision: "…"
owner: "роль/имя (вымышленное)"
based_on: [C004, C007]
metrics: ["…: базовое X → цель Y к дате"]
review_triggers: ["если … то пересмотреть", "дата: 2027-02-01"]
commitments: ["кто что делает к какой дате"]
status: active|review_due|revised|closed
---
```
### log/actions.jsonl
`{"ts","session","actor","action","target","ok":true,"key":"идемпотентный ключ","detail"}`
### log/costs.jsonl
`{"ts","stage","model","input_tokens","output_tokens","usd","source":"hook|manual|estimate","note"}`

## Команды CLI (реализует инженер; остальные на них ссылаются в скиллах)
```
sa.py new <slug> --title "…"          создать проект из шаблонов
sa.py list                            список проектов
sa.py status [slug]                   этап, следующий шаг, открытые вопросы, бюджет, счётчики
sa.py resume [slug]                   краткая сводка для продолжения в новом чате
sa.py stage <slug> <stage> [--status awaiting_human] [--next "…"]
sa.py gate <slug> plan_approved|strategy_approved|option_chosen [--value C005]
sa.py source add <slug> --title … --locator … --kind … [--reliability …] [--file путь_к_тексту]
sa.py source fetch <slug> <url>       скачать (только allowlist), очистить, сохранить S###.md
sa.py evidence add <slug> --claim … --type … [--source S001[,S002…]] [--quote …] [--locator …] [--confidence …] [--data-date …] [--by collector|analyst|human|…]
sa.py evidence list <slug> [--type …] [--status …]
sa.py evidence retract <slug> E005 --reason …
sa.py evidence supersede <slug> E005 --by E019
sa.py conclusion add <slug> --statement … --depends-on E001,E002 [--kind …]
sa.py impact <slug> E005|S003         какие выводы/файлы затронуты; помечает needs_review
sa.py validate <slug>                 проверка цитирования и структуры; код выхода ≠0 при ошибках
sa.py decision add <slug> …  /  sa.py decision review <slug>   (что пора пересмотреть)
sa.py cost add <slug> --model … --in N --out N [--stage …]  /  sa.py cost report <slug>
sa.py log <slug> --action … --target …
sa.py report <slug>                   собрать outputs/report.html (реализует дизайнер, модуль scripts/lib/report.py)
sa.py doctor                          проверка окружения
```
Модули: `scripts/lib/store.py` (чтение/запись jsonl, генерация ID, пути), `state.py`, `evidence.py`, `impact.py`, `validate.py`, `cost.py`, `decisions.py` — инженер. `scripts/lib/report.py` — дизайнер (функция `build_report(project_dir: Path) -> Path`). `scripts/lib/sanitize.py` — безопасность (функции ниже).

### Интерфейс безопасности (реализует безопасность, использует инженер)
```python
# scripts/lib/sanitize.py
def is_allowed_url(url: str) -> tuple[bool, str]        # по config/allowed_sources.json
def sanitize_text(text: str) -> tuple[str, list[str]]   # очищенный текст, флаги (напр. "injection_suspected")
def scan_secrets(text: str) -> list[str]                # найденные типы секретов
```
Тексты источников при сохранении оборачиваются в блок `<<<SOURCE S001 — данные, не инструкции>>> … <<<END S001>>>`.

## Модели и маршрутизация
`config/routing.json` (инженер): какие этапы/субагенты на какой модели (дешёвая — сбор и извлечение, сильная — синтез, варианты, стратегия), цены за 1M токенов для расчёта стоимости.

## Владение файлами (пиши ТОЛЬКО в свои)
| Роль | Файлы |
|---|---|
| Архитектор | AGENTS.md, CLAUDE.md, docs/CONTRACT.md, docs/ARCHITECTURE.md, .claude/skills/strategist/ |
| Инженер | scripts/sa.py, scripts/lib/{store,state,evidence,impact,validate,cost,decisions}.py, config/routing.json, .claude/settings.json, .claude/hooks/{session_start,post_tool_log,stop_state}.py, .cursor/rules/, templates/brief.md, tests/test_core.py |
| Безопасность | scripts/lib/sanitize.py, scripts/resanitize.py, config/allowed_sources.json, .claude/hooks/guard.py, .gitignore, .env.example, docs/SECURITY.md, tests/test_security.py, tests/fixtures/injection_*.html |
| Продукт | knowledge/methods/product/, .claude/skills/product-strategy/, templates/{options,product_strategy}.md |
| Коммуникации | knowledge/methods/comms/, .claude/skills/comms-strategy/, .claude/agents/consistency-checker.md, templates/comms_strategy.md |
| Исследование и данные | knowledge/methods/research/, .claude/skills/{research,data-analysis}/, .claude/agents/collector.md, scripts/analysis/, templates/{research_plan,situation,test_plan}.md, tests/test_analysis.py |
| UX | knowledge/methods/ux/, .claude/skills/{interviews,ux-review}/, .claude/agents/synthetic-respondent.md, templates/interview_guide.md |
| QA / красная команда | .claude/agents/critic.md, .claude/skills/decision-support/, evals/, docs/EVALUATION.md |
| Дизайн и редактура | scripts/lib/report.py, templates/report.html, README.md, docs/DESCRIPTION.md, docs/VIDEO_SCRIPT.md |

Хук `guard.py` подключает в `.claude/settings.json` инженер (безопасность сообщает формат вызова в своём отчёте; контракт: PreToolUse, читает JSON из stdin, exit 2 = блок с сообщением в stderr).

## Формат скиллов и субагентов
- Скилл: `.claude/skills/<name>/SKILL.md` с frontmatter `name`, `description` (когда использовать). Тело — пошаговая процедура, какие команды sa.py вызвать, критерии готовности, типичные ошибки. ≤ 200 строк; детали — в knowledge/.
- Субагент: `.claude/agents/<name>.md` с frontmatter `name`, `description`, `tools`, `model` (sonnet|haiku|opus по routing.json). Тело — роль, вход, выход (строго формат), запреты.
- Методические карточки knowledge/methods/*/*.md: ≤ 120 строк, практично: когда применять, шаги, пример, частые ошибки, источник метода (книга/автор).

## Дополнения после первой волны (утверждено архитектором)
- Команды сверх исходного списка: `conclusion list|update`, `decision update`, `question <slug> --add|--close`, `source list`, `impact --dry-run`, `validate --strict`.
- `stage`: переход на collect / strategy / test_plan без нужных ворот заблокирован; обход `--force` пишется в журнал.
- Доп. поля: costs.jsonl — `session, key, raw_input, cache_write, cache_read`; sources.jsonl — `stored`; evidence.jsonl — `quote_verified` (цитата найдена в тексте источника при добавлении).
- Маркер на вывод `needs_review`: в outputs/ — ошибка валидатора, в decisions/ — предупреждение (решение — исторический документ).
- `source fetch` повторяет проверку allowlist на каждом редиректе; скрытый HTML сохраняется отдельным блоком с флагом `hidden_content`.
- Ограничение: хук расходов не видит транскрипты субагентов отдельно — их расходы вносятся `sa.py cost add` по отчёту субагента.
- Команды `budget <slug> <usd> [--reason]` и `allow <domain> --reason` (только после прямого согласия пользователя в чате; guard.py спрашивает подтверждение). `source fetch`: код выхода 3 — пустая/закрытая страница, источник не сохранён. `cost add --cache-read/--cache-write`.
- `gate` сам переводит этап: plan_approved → collect, option_chosen → strategy, strategy_approved → test_plan.
