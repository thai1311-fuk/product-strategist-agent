"""Прогноз по пяти строкам таблицы брифа: без изменений → основной → менее благоприятный → цель.
Запуск: python3 -I projects/family-connect/data/forecast.py > projects/family-connect/data/forecast.md
Каждая разница с «без изменений» привязана к действию и допущению (E071). Вклад B в прибыль — из econ.md (E066).
"""

NOCHANGE = {"connected": 8000, "active_share": 5000 / 8000, "activation": 0.45, "owners": 1000, "gp": 279.0}
TARGET = {"connected": 12000, "active": 8000, "activation": 0.60, "owners": 2000, "gp": 285.0}

# Допущения о действиях (основной, менее благоприятный)
ASSUME = {
    # F: подсказки после принятия приглашения, напоминание принять приглашение, предложение в поддержке
    "groups_from_F": (600, 300),        # доп. подключённых групп к концу года
    "groups_kept_by_B": (100, 0),       # групп, не распавшихся благодаря сигналу (раскатка с октября)
    # B: сигнал у ~7 % групп в месяц (10 % событий × 70 % подходящих), реагируют 60 % / 50 %,
    # половина из них была бы активна и без сигнала
    "share_from_B_pp": (0.021, 0.0105),
    "share_from_F_pp": (0.01, 0.005),   # первое действие сразу после приглашения у новых групп
    "activation_from_F_pp": (0.07, 0.02),
    "owners_delta": (0, 0),             # владельцев в первый год не меняем
    "gp_B": (0.12, 0.03),               # млн руб., econ.md (E066)
    "new_numbers_F": (300, 0),          # новые номера близких от других операторов, набор с июля
}
RATE = 500


def n(x):
    return f"{x:,.0f}".replace(",", " ")


rows = []
for i, name in enumerate(("основной", "менее благоприятный")):
    a = {k: v[i] for k, v in ASSUME.items()}
    connected = NOCHANGE["connected"] + a["groups_from_F"] + a["groups_kept_by_B"]
    share = NOCHANGE["active_share"] + a["share_from_B_pp"] + a["share_from_F_pp"]
    active = connected * share
    activation = NOCHANGE["activation"] + a["activation_from_F_pp"]
    owners = NOCHANGE["owners"] + a["owners_delta"]
    gp_new_numbers = a["new_numbers_F"] * 3.5 * RATE / 1e6  # в среднем 3,5 оплаченных месяца при наборе с июля
    gp = NOCHANGE["gp"] + a["gp_B"] + gp_new_numbers
    rows.append((name, connected, share, active, activation, owners, gp))

out = []
p = out.append
p("# Прогноз по пяти показателям брифа (плановый год)\n")
p("Без изменений и цель — из брифа [E003]–[E007]; разницы — действия B и F при допущениях [E071]; прибыль B — [E066].\n")
p("| Показатель | Без изменений | Основной | Менее благоприятный | Цель, не ниже | Разрыв основного с целью |")
p("|---|---|---|---|---|---|")
b, w = rows[0], rows[1]
p(f"| Валовая прибыль, млн руб. | {NOCHANGE['gp']:.1f} | {b[6]:.1f} | {w[6]:.1f} | {TARGET['gp']:.1f} | {TARGET['gp'] - b[6]:.1f} |")
p(f"| Активные семейные группы за последний месяц | {n(5000)} | {n(b[3])} | {n(w[3])} | {n(TARGET['active'])} | {n(TARGET['active'] - b[3])} |")
p(f"| Подключённые семейные группы на конец года | {n(NOCHANGE['connected'])} | {n(b[1])} | {n(w[1])} | {n(TARGET['connected'])} | {n(TARGET['connected'] - b[1])} |")
p(f"| Подключённые владельцы нескольких номеров | {n(NOCHANGE['owners'])} | {n(b[5])} | {n(w[5])} | {n(TARGET['owners'])} | {n(TARGET['owners'] - b[5])} |")
p(f"| Активация за 14 дней (последний квартал) | {NOCHANGE['activation']:.0%} | {b[4]:.0%} | {w[4]:.0%} | {TARGET['activation']:.0%} | {(TARGET['activation'] - b[4]) * 100:.0f} п. п. |")
p("")
p(f"Доля активных среди подключённых: без изменений {NOCHANGE['active_share']:.1%}, основной {b[2]:.1%}, менее благоприятный {w[2]:.1%}, цель {8000 / 12000:.1%}.\n")
p("ИТОГ: в основном сценарии ни одна цель планового года не достигается; ближе всего активация; "
  "разрывы по группам и прибыли закрываются только привлечением новых групп и номеров, а не первой услугой.")
print("\n".join(out))
