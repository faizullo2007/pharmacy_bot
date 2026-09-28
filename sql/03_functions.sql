-- ============================================================
-- 03_functions.sql — ХРАНИМЫЕ ФУНКЦИИ.
--
-- Это функции, которые живут внутри базы. Python вызывает их обычным SELECT:
--   SELECT pharmacy.create_order(%s, %s)      -> models.Order.checkout()
--   SELECT pharmacy.get_revenue(%s, %s)       -> models.get_revenue()
--   SELECT * FROM pharmacy.get_top_products() -> models.get_top_products()
--   SELECT users.is_admin(%s)                 -> models.is_admin()
--   SELECT users.grant_admin(%s, %s)          -> models.grant_admin()
--   SELECT logs.cleanup_old_logs(%s)          -> models.cleanup_old_logs()
--
-- Префиксы в именах: p_ — параметр функции, v_ — локальная переменная.
-- LANGUAGE plpgsql — процедурный язык (переменные, IF, циклы).
-- LANGUAGE sql     — просто один SQL-запрос.
-- STABLE — функция только читает данные и ничего не меняет (база может это оптимизировать).
-- ============================================================


-- ---------- Оформление заказа ----------
-- Главная функция проекта. Вызывается ОДНИМ запросом и выполняется целиком
-- как одна транзакция: если на любом шаге ошибка — откатывается ВСЁ
-- (не будет "заказ создан, а склад не списан").
CREATE OR REPLACE FUNCTION pharmacy.create_order(
    p_user_id INT,                  -- кто оформляет (users.users.user_id)
    p_delivery_address VARCHAR      -- адрес доставки
)
RETURNS INT                         -- возвращает номер нового заказа
AS $$
DECLARE
    v_order_id INT;
    v_items_count INT;
    v_shortage RECORD;              -- RECORD — переменная-"строка" с любыми колонками
BEGIN
    -- Шаг 1. Сколько позиций в корзине? Если 0 — ошибка.
    SELECT COUNT(*)
    INTO v_items_count
    FROM pharmacy.cart
    WHERE user_id = p_user_id;

    IF v_items_count = 0 THEN
        RAISE EXCEPTION 'Корзина пользователя % пуста', p_user_id;
    END IF;

    -- Шаг 2. БЛОКИРОВКА товаров из корзины (FOR UPDATE OF p).
    -- Если два покупателя одновременно оформляют последний товар, второй будет ждать,
    -- пока первый закончит, и увидит уже уменьшенный остаток. Это защита от "гонки".
    -- PERFORM — выполнить SELECT, но результат не нужен.
    PERFORM 1
    FROM pharmacy.products p
    JOIN pharmacy.cart c ON c.product_id = p.product_id
    WHERE c.user_id = p_user_id
    FOR UPDATE OF p;

    -- Шаг 3. Ищем товар, которого не хватает или который деактивирован.
    SELECT p.name, p.stock_quantity
    INTO v_shortage
    FROM pharmacy.cart c
    JOIN pharmacy.products p ON p.product_id = c.product_id
    WHERE c.user_id = p_user_id
    AND (c.quantity > p.stock_quantity OR NOT p.is_active)
    LIMIT 1;

    -- FOUND — встроенная переменная: true, если предыдущий SELECT нашёл строку.
    -- Текст этой ошибки бот показывает пользователю (handlers/cart.py -> e.diag.message_primary).
    IF FOUND THEN
        RAISE EXCEPTION 'товара «%» уже не хватает (в наличии % шт.)',
            v_shortage.name, v_shortage.stock_quantity;
    END IF;

    -- Шаг 4. Создаём "шапку" заказа. RETURNING order_id INTO — сразу получаем новый номер.
    INSERT INTO pharmacy.orders(user_id, status, delivery_address)
    VALUES (p_user_id, 'pending', p_delivery_address)
    RETURNING order_id INTO v_order_id;

    -- Шаг 5. Копируем товары из корзины в позиции заказа, фиксируя текущую цену.
    -- INSERT ... SELECT — вставить сразу все строки, которые вернул SELECT.
    -- Здесь же срабатывает триггер trg_recalc_order_total и считает сумму заказа.
    INSERT INTO pharmacy.order_items(order_id, product_id, quantity, price_at_order)
    SELECT v_order_id, c.product_id, c.quantity, p.price
    FROM pharmacy.cart c
    JOIN pharmacy.products p ON p.product_id = c.product_id
    WHERE c.user_id = p_user_id;

    -- Шаг 6. Списываем товар со склада.
    UPDATE pharmacy.products p
    SET stock_quantity = p.stock_quantity - c.quantity
    FROM pharmacy.cart c
    WHERE c.user_id = p_user_id
    AND c.product_id = p.product_id;

    -- Шаг 7. Очищаем корзину.
    DELETE FROM pharmacy.cart
    WHERE user_id = p_user_id;

    RETURN v_order_id;
END;
$$ LANGUAGE plpgsql;


-- ---------- Выручка за период ----------
-- Сумма заказов между p_from и p_to. Учитываются только подтверждённые/готовые/завершённые
-- (pending ещё не подтверждён, cancelled отменён — их не считаем).
CREATE OR REPLACE FUNCTION pharmacy.get_revenue(
    p_from TIMESTAMPTZ,
    p_to TIMESTAMPTZ
)
RETURNS NUMERIC
AS $$
    -- COALESCE(..., 0) — если заказов нет, SUM вернёт NULL, а мы хотим 0.
    SELECT COALESCE(SUM(total_amount), 0)
    FROM pharmacy.orders
    WHERE status IN ('confirmed', 'ready', 'completed')
    AND created_at BETWEEN p_from AND p_to;
$$ LANGUAGE sql STABLE;


-- ---------- Самые продаваемые товары ----------
-- RETURNS TABLE — функция возвращает не одно значение, а целую таблицу.
-- p_limit INT DEFAULT 5 — параметр со значением по умолчанию.
CREATE OR REPLACE FUNCTION pharmacy.get_top_products(p_limit INT DEFAULT 5)
RETURNS TABLE (
    product_id INT,
    product_name VARCHAR,
    total_sold BIGINT
)
AS $$
    -- GROUP BY — группируем позиции заказов по товару,
    -- SUM(oi.quantity) — сколько штук каждого товара продано всего.
    -- ::BIGINT — приведение типа к BIGINT.
    -- ORDER BY ... DESC + LIMIT — самые продаваемые сверху, берём первые p_limit.
    SELECT p.product_id, p.name, SUM(oi.quantity)::BIGINT AS total_sold
    FROM pharmacy.order_items oi
    JOIN pharmacy.products p ON p.product_id = oi.product_id
    GROUP BY p.product_id, p.name
    ORDER BY total_sold DESC
    LIMIT p_limit;
$$ LANGUAGE sql STABLE;


-- ---------- Очистка старых логов ----------
-- Удаляет записи логов старше p_older_than_days дней. Возвращает, сколько удалено из bot_logs.
CREATE OR REPLACE FUNCTION logs.cleanup_old_logs(p_older_than_days INT DEFAULT 90)
RETURNS INT
AS $$
DECLARE
    v_deleted INT;
BEGIN
    -- (90 || ' days')::INTERVAL — склеиваем строку "90 days" и превращаем её в интервал времени.
    -- now() - интервал = дата 90 дней назад.
    DELETE FROM logs.bot_logs
    WHERE created_at < now() - (p_older_than_days || ' days')::INTERVAL;

    -- GET DIAGNOSTICS ... ROW_COUNT — сколько строк затронула последняя команда.
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    DELETE FROM logs.audit_logs
    WHERE changed_at < now() - (p_older_than_days || ' days')::INTERVAL;

    RETURN v_deleted;
END;
$$ LANGUAGE plpgsql
-- SECURITY DEFINER — функция выполняется с правами ВЛАДЕЛЬЦА функции, а не того, кто вызвал.
-- Так бот может удалять старые логи, хотя прямого права DELETE на таблицы логов у него нет
-- (см. 04_roles_grants.sql).
SECURITY DEFINER
-- SET search_path — фиксируем, в какой схеме искать таблицы. Это защита:
-- без неё злоумышленник мог бы подсунуть свою таблицу с таким же именем.
SET search_path = logs, pg_temp;


-- ---------- Проверка "является ли админом" ----------
-- EXISTS вернёт true, если tg_id есть в таблице users.admin.
CREATE OR REPLACE FUNCTION users.is_admin(p_tg_id BIGINT)
RETURNS BOOLEAN
AS $$
    SELECT EXISTS(
        SELECT 1
        FROM users.admin
        WHERE tg_id = p_tg_id
    );
$$ LANGUAGE sql STABLE;


-- ---------- Выдать права админа ----------
-- RETURNS VOID — функция ничего не возвращает.
-- ON CONFLICT DO NOTHING — если человек уже админ, ничего не делаем (без ошибки).
-- SECURITY DEFINER нужен, потому что у бота есть только SELECT на users.admin,
-- а вставлять туда он может только через эту функцию.
CREATE OR REPLACE FUNCTION users.grant_admin(
    p_tg_id BIGINT,
    p_note VARCHAR DEFAULT NULL
)
RETURNS VOID
AS $$
BEGIN
    INSERT INTO users.admin(tg_id, note)
    VALUES (p_tg_id, p_note)
    ON CONFLICT(tg_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = users, pg_temp;


-- ---------- Забрать права админа ----------
-- В боте сейчас не используется — для ручного вызова из SQL.
CREATE OR REPLACE FUNCTION users.revoke_admin(p_tg_id BIGINT)
RETURNS VOID
AS $$
BEGIN
    DELETE FROM users.admin
    WHERE tg_id = p_tg_id;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = users, pg_temp;
