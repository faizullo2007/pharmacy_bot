-- ============================================================
-- 02_triggers.sql — ТРИГГЕРЫ.
--
-- Триггер — это функция, которую PostgreSQL вызывает САМ, автоматически,
-- когда в таблице происходит событие (INSERT / UPDATE / DELETE).
-- Python-код их не вызывает — они срабатывают "сами по себе".
--
-- Каждый триггер состоит из двух частей:
--   1) CREATE FUNCTION ... RETURNS TRIGGER — что делать;
--   2) CREATE TRIGGER ... EXECUTE FUNCTION — когда это делать.
--
-- Внутри триггерной функции доступны:
--   NEW — строка ПОСЛЕ изменения (есть при INSERT и UPDATE)
--   OLD — строка ДО изменения   (есть при UPDATE и DELETE)
--
-- BEFORE — выполняется ДО записи (может запретить её через RAISE EXCEPTION).
-- AFTER  — выполняется ПОСЛЕ записи (для последствий: пересчёт, логи).
-- FOR EACH ROW — функция вызывается для каждой изменённой строки отдельно.
--
-- Синтаксис функций на языке plpgsql:
--   $$ ... $$     — "долларовые кавычки": весь текст функции между ними
--   DECLARE       — объявление локальных переменных (v_ — от "variable")
--   BEGIN ... END — тело функции
--   :=            — присваивание
--   RAISE EXCEPTION 'текст %', значение — выбросить ошибку; % заменяется значением.
--                   В Python она приходит как psycopg.errors.RaiseException.
-- ============================================================


-- ---------- 1. Проверка товара при добавлении в корзину ----------
-- Не даёт положить в корзину неактивный товар или больше, чем есть на складе.
-- Это "вторая линия защиты": Python (models.Order.add_item) тоже проверяет,
-- но база гарантирует правило, даже если в коде будет ошибка.
-- CREATE OR REPLACE — создать, а если уже есть — заменить.
CREATE OR REPLACE FUNCTION pharmacy.fn_validate_cart_item()
RETURNS TRIGGER
AS $$
DECLARE
    v_stock INT;          -- остаток на складе
    v_active BOOLEAN;     -- активен ли товар
BEGIN
    -- SELECT ... INTO — записать результат запроса в переменные.
    SELECT stock_quantity, is_active
    INTO v_stock, v_active
    FROM pharmacy.products
    WHERE product_id = NEW.product_id;   -- NEW.product_id — товар, который кладут в корзину

    IF NOT v_active THEN
        RAISE EXCEPTION 'Товар % недоступен для заказа', NEW.product_id;
    END IF;

    IF NEW.quantity > v_stock THEN
        RAISE EXCEPTION 'Недостаточно товара % на складе: запрошено %, в наличии %',
            NEW.product_id, NEW.quantity, v_stock;
    END IF;

    -- RETURN NEW — разрешить запись (в BEFORE-триггере это обязательно).
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Срабатывает ДО каждой вставки или изменения строки в корзине.
CREATE TRIGGER trg_validate_cart_item
BEFORE INSERT OR UPDATE ON pharmacy.cart
FOR EACH ROW
EXECUTE FUNCTION pharmacy.fn_validate_cart_item();


-- ---------- 2. Автоматический пересчёт суммы заказа ----------
-- Любое изменение позиций заказа -> пересчитываем orders.total_amount.
-- Благодаря этому Python-коду не нужно самому считать сумму заказа.
CREATE OR REPLACE FUNCTION pharmacy.fn_recalc_order_total()
RETURNS TRIGGER
AS $$
DECLARE
    v_order_id INT;
BEGIN
    -- COALESCE(a, b) — вернуть первое значение, которое не NULL.
    -- При DELETE строки NEW нет, поэтому берём OLD.order_id.
    v_order_id := COALESCE(NEW.order_id, OLD.order_id);

    UPDATE pharmacy.orders
    -- Подзапрос: сумма (количество * цена) всех позиций этого заказа; если позиций нет — 0.
    SET total_amount = (
        SELECT COALESCE(SUM(quantity * price_at_order), 0)
        FROM pharmacy.order_items
        WHERE order_id = v_order_id
    ),
    updated_at = now()
    WHERE order_id = v_order_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- Срабатывает ПОСЛЕ любой вставки/изменения/удаления позиции заказа.
CREATE TRIGGER trg_recalc_order_total
AFTER INSERT OR UPDATE OR DELETE ON pharmacy.order_items
FOR EACH ROW
EXECUTE FUNCTION pharmacy.fn_recalc_order_total();


-- ---------- 3. Журнал изменений заказа (аудит) ----------
-- Если у заказа поменялся статус или сумма — записываем "было/стало" в logs.audit_logs.
CREATE OR REPLACE FUNCTION pharmacy.fn_log_order_change()
RETURNS TRIGGER
AS $$
BEGIN
    -- IS DISTINCT FROM — "не равно", но корректно работает и с NULL
    -- (обычное "<>" с NULL даёт NULL, а не true/false).
    IF NEW.status IS DISTINCT FROM OLD.status
    OR NEW.total_amount IS DISTINCT FROM OLD.total_amount THEN
        INSERT INTO logs.audit_logs(table_name, operation, record_id, old_data, new_data)
        VALUES (
            'pharmacy.orders',
            'UPDATE',
            NEW.order_id,
            -- jsonb_build_object('ключ', значение, ...) — собрать JSON-объект.
            jsonb_build_object('status', OLD.status, 'total_amount', OLD.total_amount),
            jsonb_build_object('status', NEW.status, 'total_amount', NEW.total_amount)
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_log_order_change
AFTER UPDATE ON pharmacy.orders
FOR EACH ROW
EXECUTE FUNCTION pharmacy.fn_log_order_change();


-- ---------- 4. Защита от удаления товара, который уже покупали ----------
-- Если товар есть хоть в одном заказе — удалять нельзя (иначе сломается история заказов).
-- Вместо удаления нужно деактивировать (is_active = false) — так и делает бот
-- (models.deactivate_product).
CREATE OR REPLACE FUNCTION pharmacy.fn_protect_product_delete()
RETURNS TRIGGER
AS $$
BEGIN
    -- EXISTS (SELECT 1 ...) — "есть ли хотя бы одна такая строка?". 1 — просто заглушка.
    IF EXISTS (
        SELECT 1
        FROM pharmacy.order_items
        WHERE product_id = OLD.product_id
    ) THEN
        RAISE EXCEPTION
            'Товар "%" нельзя удалить — он есть в заказах. Используйте is_active = false',
            OLD.name;
    END IF;

    -- В BEFORE DELETE нужно вернуть OLD, чтобы удаление продолжилось.
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_product_delete
BEFORE DELETE ON pharmacy.products
FOR EACH ROW
EXECUTE FUNCTION pharmacy.fn_protect_product_delete();


-- ---------- 5. Возврат товара на склад при отмене заказа ----------
-- Когда админ ставит статус "cancelled", количество из заказа возвращается в stock_quantity.
-- (При оформлении заказа оно было списано функцией pharmacy.create_order.)
CREATE OR REPLACE FUNCTION pharmacy.fn_restore_stock_on_cancel()
RETURNS TRIGGER
AS $$
BEGIN
    -- Только при ПЕРЕХОДЕ в cancelled (чтобы не вернуть товар дважды).
    IF NEW.status = 'cancelled'
    AND OLD.status IS DISTINCT FROM 'cancelled' THEN
        -- UPDATE ... FROM — обновление с использованием данных из другой таблицы:
        -- к остатку каждого товара прибавляем количество из позиций этого заказа.
        UPDATE pharmacy.products p
        SET stock_quantity = p.stock_quantity + oi.quantity
        FROM pharmacy.order_items oi
        WHERE oi.order_id = NEW.order_id
        AND oi.product_id = p.product_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_restore_stock_on_cancel
AFTER UPDATE ON pharmacy.orders
FOR EACH ROW
EXECUTE FUNCTION pharmacy.fn_restore_stock_on_cancel();
