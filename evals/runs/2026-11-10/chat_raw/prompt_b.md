Ты — опытный продуктовый стратег. Ниже запрос руководителя и материалы.
Подготовь: картину ситуации (что известно, откуда, чего не знаем), не менее трёх разных вариантов
решения с возражениями к каждому, рекомендацию, продуктовую и коммуникационную стратегию,
план проверки (что измеряем, с чем сравниваем, срок, риски). Указывай источник для каждого
утверждения. Отвечай на русском.

**Запрос руководителя:**
«Мы сделали „Орбита Бизнес-Касса“ — онлайн-касса в смартфоне + приём оплат по QR + простой учёт для тех, кто торгует сам. Наши клиенты — частные абоненты, малый бизнес почти не знаем. Кому продавать в первую очередь и через какие каналы? Нужна стратегия выхода на 2027 год».

**Продукт:** приложение для Android/iOS, приём оплаты по QR (СБП), фискализация через облачную кассу партнёра, учёт продаж; цена — гипотеза 490 ₽/мес, первые 3 месяца бесплатно.

**Получатель:** руководитель направления новых сервисов; решение — какие 1–2 сегмента и канала выбрать для пилота на I квартал 2027.

**Ограничения:** бюджет агента $5; маркетинговый бюджет пилота — небольшой (сумму агент не знает и должен спросить или считать в долях); нельзя использовать данные абонентов без согласия.

**Доступные материалы:**
- `materials/product_onepager.md` — описание продукта;
- `materials/field_notes.md` — заметки команды после 6 встреч с продавцами (вымышленные, без ПДн);
- `materials/pilot_signups.csv` — заявки с тестового лендинга за 3 недели (вымышленные);
- открытые источники из allowlist (статистика, законодательство, сайты конкурентов) — агент ищет сам.


=== field_notes.md ===
# Заметки с 6 встреч (вымышленные, без персональных данных)

1. **Продавец мёда на ярмарках выходного дня** — платёжный режим: самозанятый. «Чек формирую в приложении налоговой, мне хватает. Перевод по номеру — да, все так платят». Интерес к кассе — низкий.
2. **Мастер маникюра на дому** — самозанятая. «Клиентки платят переводом. Учёт веду в заметках». Интерес — средний к записи клиентов, а не к кассе.
3. **Киоск с выпечкой у метро** — ИП, есть касса банка с терминалом. «Терминал аренда дорого, но карта нужна — половина платит картой». Интерес — к снижению комиссии, но без карт не перейдёт.
4. **Продавец сувениров онлайн + маркетплейсы** — ИП. «Касса нужна для сайта, у меня маркировка». Продукт не подходит.
5. **Репетитор английского** — самозанятый. «Мне касса не нужна». Интерес — нет.
6. **Небольшая кофейня-точка (1 продавец)** — ИП на упрощёнке, сейчас касса старого образца. «Если бесплатно три месяца и не надо покупать устройство — попробую. Но мне нужно, чтобы чек был быстро, очередь». Интерес — высокий.

Команда: «самозанятым касса, похоже, не нужна вообще — у них есть приложение налоговой».


=== pilot_signups.csv ===
signup_id,week,segment_self_reported,tax_regime,region,source,wants_card_payments,called_back
L001,2026-W40,beauty_home,unknown,Екатеринбург,orbita_app_banner,unknown,yes
L002,2026-W40,beauty_home,usn,Москва,orbita_app_banner,no,yes
L003,2026-W42,food_kiosk,unknown,Москва,sms_to_subscribers,yes,yes
L004,2026-W42,market_trader,npd,Самара,search_ads,yes,yes
L005,2026-W40,food_kiosk,patent,Новосибирск,orbita_app_banner,yes,no
L006,2026-W42,market_trader,usn,Москва,search_ads,unknown,yes
L007,2026-W42,beauty_home,npd,Самара,orbita_app_banner,yes,no
L008,2026-W41,online_seller,patent,Новосибирск,search_ads,no,no
L009,2026-W40,food_kiosk,usn,Москва,search_ads,unknown,no
L010,2026-W42,services_tutor,unknown,Казань,search_ads,yes,yes
L011,2026-W40,market_trader,patent,Санкт-Петербург,sms_to_subscribers,no,yes
L012,2026-W40,other,patent,Казань,search_ads,unknown,no
L013,2026-W41,market_trader,npd,Москва,sms_to_subscribers,no,yes
L014,2026-W42,food_kiosk,patent,Екатеринбург,search_ads,unknown,no
L015,2026-W41,food_kiosk,patent,Москва,sms_to_subscribers,no,yes
L016,2026-W41,market_trader,npd,Санкт-Петербург,partner_blog,yes,yes
L017,2026-W41,beauty_home,npd,Санкт-Петербург,orbita_app_banner,unknown,no
L018,2026-W41,services_tutor,patent,Екатеринбург,orbita_app_banner,no,no
L019,2026-W40,other,npd,Санкт-Петербург,orbita_app_banner,unknown,yes
L020,2026-W42,food_kiosk,usn,Казань,orbita_app_banner,yes,no
L021,2026-W42,market_trader,patent,Санкт-Петербург,search_ads,unknown,yes
L022,2026-W42,beauty_home,unknown,Новосибирск,orbita_app_banner,yes,no
L023,2026-W40,market_trader,usn,Москва,sms_to_subscribers,no,yes
L024,2026-W42,food_kiosk,npd,Москва,orbita_app_banner,yes,yes
L025,2026-W42,other,npd,Москва,sms_to_subscribers,unknown,no
L026,2026-W41,food_kiosk,patent,Самара,orbita_app_banner,yes,yes
L027,2026-W41,services_tutor,unknown,Новосибирск,orbita_app_banner,yes,yes
L028,2026-W42,online_seller,patent,Новосибирск,sms_to_subscribers,yes,yes
L029,2026-W42,food_kiosk,patent,Санкт-Петербург,search_ads,yes,no
L030,2026-W40,other,patent,Самара,orbita_app_banner,yes,no
L031,2026-W42,online_seller,patent,Екатеринбург,orbita_app_banner,yes,yes
L032,2026-W42,services_tutor,usn,Санкт-Петербург,search_ads,no,yes
L033,2026-W41,other,unknown,Казань,orbita_app_banner,unknown,no
L034,2026-W42,beauty_home,patent,Казань,orbita_app_banner,yes,yes
L035,2026-W41,beauty_home,usn,Новосибирск,search_ads,unknown,yes
L036,2026-W42,beauty_home,patent,Екатеринбург,orbita_app_banner,unknown,yes
L037,2026-W42,other,usn,Новосибирск,sms_to_subscribers,no,no
L038,2026-W42,food_kiosk,unknown,Новосибирск,orbita_app_banner,yes,yes
L039,2026-W40,food_kiosk,npd,Санкт-Петербург,search_ads,no,yes
L040,2026-W42,market_trader,unknown,Екатеринбург,sms_to_subscribers,yes,yes
L041,2026-W42,food_kiosk,npd,Самара,partner_blog,yes,no
L042,2026-W40,other,usn,Москва,orbita_app_banner,no,yes
L043,2026-W41,online_seller,patent,Самара,orbita_app_banner,yes,yes
L044,2026-W41,other,unknown,Екатеринбург,search_ads,unknown,no
L045,2026-W42,services_tutor,usn,Самара,orbita_app_banner,unknown,yes
L046,2026-W40,services_tutor,npd,Санкт-Петербург,orbita_app_banner,no,yes
L047,2026-W41,market_trader,unknown,Москва,sms_to_subscribers,yes,yes
L048,2026-W40,food_kiosk,npd,Самара,search_ads,yes,yes
L049,2026-W42,beauty_home,usn,Екатеринбург,orbita_app_banner,unknown,no
L050,2026-W40,market_trader,patent,Самара,sms_to_subscribers,yes,no
L051,2026-W40,food_kiosk,unknown,Новосибирск,orbita_app_banner,unknown,yes
L052,2026-W40,beauty_home,patent,Москва,sms_to_subscribers,yes,no
L053,2026-W40,food_kiosk,unknown,Санкт-Петербург,partner_blog,yes,no
L054,2026-W40,services_tutor,usn,Санкт-Петербург,partner_blog,unknown,no
L055,2026-W40,beauty_home,patent,Казань,orbita_app_banner,no,yes
L056,2026-W41,beauty_home,unknown,Екатеринбург,orbita_app_banner,no,no
L057,2026-W40,market_trader,npd,Санкт-Петербург,sms_to_subscribers,yes,yes


=== product_onepager.md ===
# «Орбита Бизнес-Касса» — одностраничник (вымышленный)

- Касса в смартфоне: чек покупателю по SMS/e-mail, без отдельного устройства.
- Приём оплаты по QR (СБП): комиссия — гипотеза 0,6 %.
- Учёт: продажи за день, остатки (простые), выгрузка для бухгалтера.
- Для кого (гипотеза команды): «все, кто торгует сам — от мастеров маникюра до фермеров на рынке».
- Отличие (гипотеза): «просто, как мессенджер; от оператора, которому уже доверяют».
- Что не умеет: эквайринг банковских карт через терминал, маркировка товаров, работа нескольких продавцов.

