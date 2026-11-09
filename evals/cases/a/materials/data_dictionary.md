# Словарь данных «Волна» (вымышленные данные)

## users.csv — одна строка на установку
| поле | смысл |
|---|---|
| user_id | анонимный идентификатор |
| signup_date | дата установки |
| platform | android / ios |
| channel | organic, paid_promo (платная кампания), referral (пригласи друга) |
| app_version | версия при установке: 3.1, 3.2 (Android), 3.1-ios |

## events.csv — журнал событий
| поле | смысл |
|---|---|
| event | install, onboarding_start, onboarding_complete, registration_error, session_start, listen, subscribe |
| value | для listen — минуты; для subscribe — цена, ₽; для registration_error — код ошибки |

Наблюдение заканчивается 2026-10-31. Удержание D7 в компании считают как «была сессия на 7-й день или позже в пределах 30 дней».
Известно, что журнал иногда дублирует session_start.
