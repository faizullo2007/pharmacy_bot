-- ============================================================
-- 04_roles_grants.sql — ПОЛЬЗОВАТЕЛЬ БАЗЫ ДЛЯ БОТА И ЕГО ПРАВА.
--
-- Бот подключается к базе НЕ как суперпользователь postgres, а как отдельная роль
-- pharmacy_bot_app с минимально нужными правами (принцип наименьших привилегий).
-- Если токен/пароль бота украдут — злоумышленник не сможет удалить таблицы,
-- выдать себе админку напрямую или стереть логи.
--
-- GRANT  — выдать право;  REVOKE — забрать право.
-- ============================================================

-- Создаём роль (пользователя БД). LOGIN — может подключаться.
-- ПЕРЕД ЗАПУСКОМ замените 'ВАШ_ПАРОЛЬ' на свой пароль — он должен
-- совпадать с DB_PASSWORD в файле .env.
CREATE ROLE pharmacy_bot_app WITH LOGIN PASSWORD 'ВАШ_ПАРОЛЬ';

-- CONNECT — право подключаться к базе; USAGE на схему — право "заходить" в схему.
GRANT CONNECT ON DATABASE pharmacy_bot TO pharmacy_bot_app;
GRANT USAGE ON SCHEMA users, pharmacy, logs TO pharmacy_bot_app;

-- Пользователи: читать, добавлять, изменять (но НЕ удалять).
GRANT SELECT, INSERT, UPDATE ON users.tg_users, users.users TO pharmacy_bot_app;
-- Админы: ТОЛЬКО чтение. Добавлять админов можно лишь через функцию users.grant_admin.
GRANT SELECT ON users.admin TO pharmacy_bot_app;
-- SEQUENCE (последовательность) — счётчик, который выдаёт новые значения для SERIAL-колонок.
-- Без этого права бот не сможет вставлять строки с автоматическим id.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA users TO pharmacy_bot_app;

-- Аптека (товары, корзина, заказы): полный доступ к данным.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pharmacy TO pharmacy_bot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA pharmacy TO pharmacy_bot_app;

-- Логи: только читать и добавлять. Изменить или удалить запись лога бот не может.
GRANT SELECT, INSERT ON logs.bot_logs, logs.audit_logs TO pharmacy_bot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA logs TO pharmacy_bot_app;

-- EXECUTE — право вызывать функции из 03_functions.sql.
-- В скобках — типы параметров (в PostgreSQL функция определяется именем + типами).
GRANT EXECUTE ON FUNCTION pharmacy.create_order(INT, VARCHAR) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION pharmacy.get_revenue(TIMESTAMPTZ, TIMESTAMPTZ) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION pharmacy.get_top_products(INT) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION logs.cleanup_old_logs(INT) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION users.is_admin(BIGINT) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION users.grant_admin(BIGINT, VARCHAR) TO pharmacy_bot_app;
GRANT EXECUTE ON FUNCTION users.revoke_admin(BIGINT) TO pharmacy_bot_app;

-- PUBLIC — "все пользователи базы". Забираем у них любые права на наши схемы,
-- чтобы доступ был только у тех, кому выдали явно.
REVOKE ALL ON SCHEMA users, pharmacy, logs FROM PUBLIC;
