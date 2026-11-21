"""Q1: разложение отставания базового года и разрывов планового года (учебные числа брифа S001).
Запуск: python3 projects/family-connect/data/q1_gap.py > projects/family-connect/data/q1_gap.md
Только стандартная библиотека. Все входные числа — из таблицы брифа [E003]–[E007], [E011].
"""

PLAN = {"connected": 7500, "active": 5000, "activation": 0.50, "gp": 282.0}
EST = {"connected": 6500, "active": 4000, "activation": 0.42, "gp": 275.0}
NOCHANGE = {"connected": 8000, "active": 5000, "activation": 0.45, "gp": 279.0, "owners": 1000}
TARGET = {"connected": 12000, "active": 8000, "activation": 0.60, "gp": 285.0, "owners": 2000}
OWNERS_START = 800
RATE = 500  # руб. валовой прибыли за доп. оплаченный месяц номера


def n(x):
    """Число с пробелом-разделителем тысяч."""
    return f"{x:,.0f}".replace(",", " ")


def share(d):
    return d["active"] / d["connected"]


out = []
p = out.append
p("# Q1 — где отставание и какие разрывы (расчёт по брифу)\n")
p("Источник чисел: таблица брифа [S001] (доказательства E003–E007, E011). Скрипт: `data/q1_gap.py`.\n")

# 1. Базовый год: план vs оценка
gap_active = PLAN["active"] - EST["active"]
eff_groups = (PLAN["connected"] - EST["connected"]) * share(PLAN)
eff_share = EST["connected"] * (share(PLAN) - share(EST))
p("## 1. Базовый год: почему 4 000 активных групп вместо 5 000\n")
p("| Показатель | План | Оценка |")
p("|---|---|---|")
p(f"| Подключённые группы | {n(PLAN['connected'])} | {n(EST['connected'])} |")
p(f"| Активные за последний месяц | {n(PLAN['active'])} | {n(EST['active'])} |")
p(f"| Доля активных среди подключённых | {share(PLAN):.1%} | {share(EST):.1%} |")
p(f"| Активация за 14 дней | {PLAN['activation']:.0%} | {EST['activation']:.0%} |\n")
p("Разложение (активные = подключённые × доля активных; эффект числа групп считаем при плановой доле):\n")
p(f"- отставание по активным: {gap_active} групп;")
p(f"- из-за меньшего числа подключённых групп: ({PLAN['connected']} − {EST['connected']}) × {share(PLAN):.4f} = {eff_groups:.0f} групп ({eff_groups / gap_active:.0%});")
p(f"- из-за меньшей доли действующих групп: {EST['connected']} × ({share(PLAN):.4f} − {share(EST):.4f}) = {eff_share:.0f} групп ({eff_share / gap_active:.0%});")
p(f"- проверка: {eff_groups:.0f} + {eff_share:.0f} = {eff_groups + eff_share:.0f}.\n")
p("ИТОГ 1: две трети отставания по активным группам — меньше подключённых групп (приток и/или уход), треть — подключённые группы реже действуют; активация ниже плана на 8 п. п.\n")

# 2. Плановый год: разрывы
p("## 2. Плановый год: разрывы между «без изменений» и целью\n")
p(f"- Подключённые группы: нужно {TARGET['connected']} − {NOCHANGE['connected']} = {TARGET['connected'] - NOCHANGE['connected']} сверх прогноза без изменений; чистый прирост к началу года {TARGET['connected'] - EST['connected']}.")
p(f"- Доля активных: без изменений {share(NOCHANGE):.1%}, цель {share(TARGET):.1%} (+{(share(TARGET) - share(NOCHANGE)) * 100:.1f} п. п.).")
need_conn_at_nochange_share = TARGET["active"] / share(NOCHANGE)
need_share_at_nochange_conn = TARGET["active"] / NOCHANGE["connected"]
p(f"- Если доля активных останется {share(NOCHANGE):.1%}, для 8 000 активных нужно {n(need_conn_at_nochange_share)} подключённых групп.")
p(f"- Если подключённых останется 8 000, для 8 000 активных нужна доля {need_share_at_nochange_conn:.0%} — недостижимо.")
for conn in (9000, 10000, 12000):
    p(f"- При {n(conn)} подключённых и доле {share(NOCHANGE):.1%} активных было бы {n(conn * share(NOCHANGE))}; при доле 66,7 % — {n(conn * 2 / 3)}.")
p("")
gp_gap = TARGET["gp"] - NOCHANGE["gp"]
months = gp_gap * 1e6 / RATE
p(f"- Валовая прибыль: разрыв {gp_gap:.0f} млн руб. = {n(months)} доп. оплаченных месяцев по {RATE} руб. без новых прямых расходов.")
p(f"- Для сравнения масштаба: {n(months)} месяцев — это {n(months / 12)} номеров, удержанных на весь год, или {n(months / 6)} номеров, удержанных на полгода.")
p(f"- Владельцы нескольких номеров: без изменений {NOCHANGE['owners']}, цель {TARGET['owners']} (+{TARGET['owners'] - NOCHANGE['owners']}); от доступных 40 000 это {NOCHANGE['owners'] / 40000:.1%} → {TARGET['owners'] / 40000:.1%}.")
p(f"- Охват семей: 12 000 групп ≈ {TARGET['connected'] / 100000:.0%} из 100 000 доступных домохозяйств; без изменений {NOCHANGE['connected'] / 100000:.0%}.\n")
p("ИТОГ 2: цель по активным требует и роста числа групп, и роста доли действующих; прибыль +6 млн руб. — 12 000 доп. оплаченных месяцев, т. е. ≈ 1 000 номеров, удержанных весь год.")

print("\n".join(out))
