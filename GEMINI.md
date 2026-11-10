# Стратег Фабрики продуктов (Gemini CLI)

Все правила и процесс — в `AGENTS.md` (единый источник), контракты — в `docs/CONTRACT.md`. Здесь они не дублируются.

@./AGENTS.md

Хуков нет: начало работы — `python3 scripts/sa.py session start <slug>`, конец — `python3 scripts/sa.py session end <slug> --model <имя> --in N --out N`. Подробности — `docs/PORTABILITY.md`.
