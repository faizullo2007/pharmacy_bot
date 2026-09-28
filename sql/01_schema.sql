-- ============================================================
-- 01_schema.sql — СТРУКТУРА БАЗЫ: схемы, таблицы, индексы.
--
-- Таблицы разложены по трём СХЕМАМ (схема = "папка" для таблиц внутри базы):
--   users    — пользователи и админы
--   pharmacy — товары, корзина, заказы
--   logs     — журналы действий
--
-- Связи между таблицами (стрелка = внешний ключ REFERENCES):
--   users.users.tg_id               -> users.tg_users.tg_id
--   users.admin.tg_id               -> users.tg_users.tg_id
--   pharmacy.products.category_id   -> pharmacy.categories.category_id
--   pharmacy.cart.user_id           -> users.users.user_id
--   pharmacy.cart.product_id        -> pharmacy.products.product_id
--   pharmacy.orders.user_id         -> users.users.user_id
--   pharmacy.order_items.order_id   -> pharmacy.orders.order_id
--   pharmacy.order_items.product_id -> pharmacy.products.product_id
--
-- Словарь терминов:
--   PRIMARY KEY   — первичный ключ: уникальный идентификатор строки
--   SERIAL        — целое число, которое база сама увеличивает: 1, 2, 3...
--   BIGSERIAL     — то же, но для очень больших чисел
--   REFERENCES    — внешний ключ: значение обязано существовать в другой таблице
--   NOT NULL      — поле обязательно для заполнения
--   UNIQUE        — значение не может повторяться
--   DEFAULT       — значение по умолчанию, если его не передали
--   CHECK (...)   — условие, которое база проверяет при каждой записи
--   ON DELETE CASCADE  — удалили "родителя" -> удалятся и связанные строки
--   ON DELETE RESTRICT — запрещено удалять "родителя", пока есть связанные строки
--   TIMESTAMPTZ   — дата+время с часовым поясом; now() — текущий момент
--   VARCHAR(n)    — строка длиной до n символов; TEXT — строка без ограничения
--   NUMERIC(10,2) — точное число: 10 цифр всего, 2 после запятой (для денег)
-- ============================================================

-- IF NOT EXISTS — не выдавать ошибку, если схема уже есть.
CREATE SCHEMA IF NOT EXISTS users;

-- Все, кто хоть раз писал боту /start (данные из Telegram-аккаунта).
CREATE TABLE users.tg_users (
    tg_id BIGINT PRIMARY KEY,          -- Telegram-ID (большое число, поэтому BIGINT)
    tg_first_name VARCHAR(255),        -- имя в Telegram
    tg_last_name VARCHAR(255),         -- фамилия в Telegram
    tg_username VARCHAR(255),          -- @username
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()   -- когда впервые появился
);

-- Зарегистрированные покупатели (заполнили анкету в боте).
CREATE TABLE users.users (
    user_id SERIAL PRIMARY KEY,        -- внутренний номер пользователя (используется в корзине и заказах)
    -- Один Telegram-аккаунт = один профиль (UNIQUE).
    -- CASCADE: удалили запись из tg_users -> профиль удалится тоже.
    tg_id BIGINT NOT NULL UNIQUE REFERENCES users.tg_users(tg_id) ON DELETE CASCADE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    -- SMALLINT — маленькое целое число; CHECK — возраст от 0 до 120.
    age SMALLINT NOT NULL CHECK (age BETWEEN 0 AND 120),
    address VARCHAR(255) NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Администраторы. Если tg_id есть в этой таблице — человек админ.
CREATE TABLE users.admin (
    admin_id SERIAL PRIMARY KEY,
    tg_id BIGINT NOT NULL UNIQUE REFERENCES users.tg_users(tg_id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- когда выданы права
    note VARCHAR(255)                                -- заметка (необязательно)
);

CREATE SCHEMA IF NOT EXISTS pharmacy;

-- Категории товаров ("Обезболивающие", "Витамины" ...).
CREATE TABLE pharmacy.categories (
    category_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,   -- UNIQUE: двух категорий с одним именем быть не может
    description TEXT
);

-- Товары.
CREATE TABLE pharmacy.products (
    product_id SERIAL PRIMARY KEY,
    -- RESTRICT: нельзя удалить категорию, пока в ней есть товары.
    category_id INT NOT NULL REFERENCES pharmacy.categories(category_id) ON DELETE RESTRICT,
    name VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(150),                              -- производитель (может быть пустым)
    description TEXT,
    price NUMERIC(10,2) NOT NULL CHECK (price >= 0),        -- цена не может быть отрицательной
    stock_quantity INT NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),  -- остаток на складе
    requires_prescription BOOLEAN NOT NULL DEFAULT false,   -- нужен ли рецепт (true/false)
    is_active BOOLEAN NOT NULL DEFAULT true,                -- false = товар скрыт из каталога
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Корзина: одна строка = один товар в корзине одного пользователя.
CREATE TABLE pharmacy.cart (
    cart_id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users.users(user_id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES pharmacy.products(product_id) ON DELETE CASCADE,
    quantity INT NOT NULL CHECK (quantity > 0),             -- количество строго больше нуля
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Составной UNIQUE: один и тот же товар у одного пользователя — только одной строкой.
    -- На это опирается "INSERT ... ON CONFLICT (user_id, product_id)" в models.py.
    UNIQUE(user_id, product_id)
);

-- Заказы ("шапка" заказа).
CREATE TABLE pharmacy.orders (
    order_id SERIAL PRIMARY KEY,
    -- RESTRICT: нельзя удалить пользователя, у которого есть заказы.
    user_id INT NOT NULL REFERENCES users.users(user_id) ON DELETE RESTRICT,
    -- Статус может быть только одним из пяти значений (CHECK ... IN (...)).
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'ready', 'completed', 'cancelled')),
    -- Сумму заказа считает триггер trg_recalc_order_total (02_triggers.sql).
    total_amount NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    delivery_address VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Позиции заказа: какие товары и сколько входят в заказ.
CREATE TABLE pharmacy.order_items (
    order_item_id SERIAL PRIMARY KEY,
    -- CASCADE: удалили заказ -> удалятся и его позиции.
    order_id INT NOT NULL REFERENCES pharmacy.orders(order_id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES pharmacy.products(product_id) ON DELETE RESTRICT,
    quantity INT NOT NULL CHECK (quantity > 0),
    -- Цена "замораживается" на момент покупки: если потом цена товара изменится,
    -- в старом заказе останется старая цена.
    price_at_order NUMERIC(10,2) NOT NULL CHECK (price_at_order >= 0)
);

-- ИНДЕКСЫ — ускоряют поиск по колонке (как оглавление в книге).
-- Ставим на колонки, по которым часто ищем в WHERE и JOIN.
CREATE INDEX idx_products_category ON pharmacy.products(category_id);   -- товары категории
CREATE INDEX idx_cart_user ON pharmacy.cart(user_id);                   -- корзина пользователя
CREATE INDEX idx_orders_user ON pharmacy.orders(user_id);               -- заказы пользователя
CREATE INDEX idx_order_items_order ON pharmacy.order_items(order_id);   -- позиции заказа

CREATE SCHEMA IF NOT EXISTS logs;

-- Журнал действий пользователей в боте (например, "checkout"). Пишет models.log_action().
CREATE TABLE logs.bot_logs (
    log_id BIGSERIAL PRIMARY KEY,
    tg_id BIGINT,                      -- кто сделал (без внешнего ключа — лог хранится независимо)
    action VARCHAR(100) NOT NULL,      -- что сделал
    details TEXT,                      -- подробности
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Журнал аудита: изменения данных в таблицах. Заполняется триггером trg_log_order_change.
CREATE TABLE logs.audit_logs (
    log_id BIGSERIAL PRIMARY KEY,
    table_name VARCHAR(100) NOT NULL,  -- в какой таблице изменение
    operation VARCHAR(10) NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    record_id INT,                     -- id изменённой строки
    old_data JSONB,                    -- данные ДО изменения (в формате JSON)
    new_data JSONB,                    -- данные ПОСЛЕ изменения
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bot_logs_tg_id ON logs.bot_logs(tg_id);
CREATE INDEX idx_audit_logs_table ON logs.audit_logs(table_name);
