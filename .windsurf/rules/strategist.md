---
trigger: always_on
---

# Стратег Фабрики продуктов

Следуй `AGENTS.md` в корне репозитория (единый источник правил и процесса) и контрактам из `docs/CONTRACT.md`. Процесс здесь не дублируется.

Хуков нет: начало работы — `python3 scripts/sa.py session start <slug>`, конец — `python3 scripts/sa.py session end <slug> --model <имя> --in N --out N`. Процедуры — `.claude/skills/*/SKILL.md`, роли субагентов — `.claude/agents/*.md` (обычный markdown). Подробности — `docs/PORTABILITY.md`.
