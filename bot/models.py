"""
models.py — РАБОТА С ДАННЫМИ (слой "модели").

Здесь собраны ВСЕ SQL-запросы бота. Обработчики (handlers/) сами SQL не пишут,
а вызывают функции и классы отсюда. Так код разделён на уровни:

    handlers/*  ->  models.py  ->  db.py  ->  PostgreSQL
    (что показать   (какие данные  (как отправить
     пользователю)   взять/записать) запрос в базу)

Что внутри:
    class Order — корзина конкретного пользователя и оформление заказа
    class User  — обычный пользователь (профиль, регистрация, меню)
    class Admin — администратор (наследник User, в меню есть "Админ-панель")
    функции     — категории, товары, заказы, статистика, логи

Во всех SQL-запросах %s — это заполнитель. Реальные значения передаются
вторым аргументом (кортежем), и psycopg подставляет их безопасно.
Параметр `db` везде — это объект Database из db.py.
"""

# psycopg нужен здесь только ради класса ошибки psycopg.errors.RaiseException
# (её выбрасывает PostgreSQL, когда в триггере срабатывает RAISE EXCEPTION).
import psycopg

# ============================================================
#  КОРЗИНА И ЗАКАЗ
# ============================================================
# Объект Order = корзина одного пользователя (user_id).
# Создаётся в handlers/catalog.py и handlers/cart.py так: Order(db, profile["user_id"])
class Order:
    def __init__(self, db, user_id):
        self.db = db              # подключение к БД
        self.user_id = user_id    # id пользователя из таблицы users.users (НЕ Telegram-ID)

    # Добавить товар в корзину. quantity=1 — значение по умолчанию.
    # Возвращает кортеж из двух значений: (успех: True/False, текст ошибки или None).
    async def add_item(self, product_id, quantity=1):
        if quantity < 1:
            return False, "Количество должно быть больше нуля"

        # Проверяем, что товар существует и не деактивирован админом.
        product = await get_product(self.db, product_id)
        if product is None or not product["is_active"]:
            return False, "Товар недоступен"

        stock = product["stock_quantity"]   # сколько штук на складе
        if stock <= 0:
            return False, "Товара нет в наличии"

        # Сколько этого товара УЖЕ лежит в корзине у пользователя?
        row = await self.db.fetch_one(
            "SELECT quantity FROM pharmacy.cart WHERE user_id = %s AND product_id = %s",
            (self.user_id, product_id),
        )
        # Тернарный оператор: если строка найдена — берём quantity, иначе 0.
        in_cart = row["quantity"] if row else 0
        # Нельзя положить больше, чем есть на складе (с учётом уже лежащего в корзине).
        if in_cart + quantity > stock:
            if in_cart:
                return False, f"В наличии только {stock} шт., в корзине уже {in_cart} шт."
            return False, f"В наличии только {stock} шт."

        try:
            # INSERT ... ON CONFLICT — "UPSERT" (вставить или обновить):
            #   - если такого товара у пользователя в корзине нет — добавляем строку;
            #   - если уже есть (конфликт по UNIQUE(user_id, product_id)) — увеличиваем количество.
            # EXCLUDED — это та строка, которую мы пытались вставить.
            await self.db.execute(
                """
                INSERT INTO pharmacy.cart (user_id, product_id, quantity)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, product_id)
                    DO UPDATE SET quantity = pharmacy.cart.quantity + EXCLUDED.quantity
                """,
                (self.user_id, product_id, quantity),
            )
        # Двойная защита: даже если проверки выше пропустили, триггер в базе
        # (trg_validate_cart_item, sql/02_triggers.sql) не даст положить больше, чем на складе.
        except psycopg.errors.RaiseException:
            return False, "Недостаточно товара на складе"
        return True, None

    # Установить точное количество товара в корзине (кнопки ➖ / ➕ в корзине).
    async def set_item_quantity(self, product_id, quantity):
        # Если количество стало 0 или меньше — просто убираем товар из корзины.
        if quantity <= 0:
            await self.remove_item(product_id)
            return True, None

        product = await get_product(self.db, product_id)
        if product is None or not product["is_active"]:
            return False, "Товар недоступен"
        if quantity > product["stock_quantity"]:
            return False, f"В наличии только {product['stock_quantity']} шт."

        try:
            await self.db.execute(
                "UPDATE pharmacy.cart SET quantity = %s WHERE user_id = %s AND product_id = %s",
                (quantity, self.user_id, product_id),
            )
        except psycopg.errors.RaiseException:
            return False, "Недостаточно товара на складе"
        return True, None

    # Получить содержимое корзины.
    # JOIN — соединяем таблицу cart с products, чтобы сразу получить название и цену.
    # "c" и "p" — короткие псевдонимы (алиасы) таблиц.
    # AS product_name — переименовываем колонку в результате.
    # ORDER BY c.added_at — в порядке добавления.
    async def get_items(self):
        return await self.db.fetch_all(
            """
            SELECT c.product_id, p.name AS product_name, p.price, c.quantity
            FROM pharmacy.cart c
            JOIN pharmacy.products p ON p.product_id = c.product_id
            WHERE c.user_id = %s
            ORDER BY c.added_at
            """,
            # (self.user_id,) — кортеж из ОДНОГО элемента; запятая обязательна,
            # без неё это были бы просто скобки вокруг числа.
            (self.user_id,),
        )

    # Общая сумма корзины: для каждого товара цена * количество, затем sum() складывает всё.
    async def get_total(self):
        items = await self.get_items()
        return sum(item["price"] * item["quantity"] for item in items)

    # Удалить один товар из корзины (кнопка ❌).
    async def remove_item(self, product_id):
        await self.db.execute(
            "DELETE FROM pharmacy.cart WHERE user_id = %s AND product_id = %s",
            (self.user_id, product_id),
        )

    # Очистить всю корзину (кнопка "🗑 Очистить корзину").
    async def clear(self):
        await self.db.execute("DELETE FROM pharmacy.cart WHERE user_id = %s", (self.user_id,))

    # Оформить заказ. Возвращает номер нового заказа или None, если корзина пуста.
    async def checkout(self, address):
        items = await self.get_items()
        if not items:
            return None
        # Вся сложная логика — в SQL-функции pharmacy.create_order (sql/03_functions.sql):
        # она проверяет остатки, создаёт заказ, переносит товары, списывает склад, чистит корзину.
        # Всё это выполняется как одно целое: либо всё, либо ничего.
        row = await self.db.fetch_one(
            "SELECT pharmacy.create_order(%s, %s) AS order_id",
            (self.user_id, address),
        )
        return row["order_id"]


# ============================================================
#  ПОЛЬЗОВАТЕЛИ
# ============================================================
# Обычный пользователь бота. tg_id — его ID в Telegram.
class User:
    def __init__(self, db, tg_id):
        self.db = db
        self.tg_id = tg_id

    # Сохраняем/обновляем данные из Telegram-аккаунта (имя, фамилия, @username).
    # Вызывается при каждом /start. ON CONFLICT ... DO UPDATE — если запись уже есть, обновляем.
    async def save_tg_info(self, first_name, last_name, username):
        await self.db.execute(
            """
            INSERT INTO users.tg_users (tg_id, tg_first_name, tg_last_name, tg_username)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tg_id) DO UPDATE SET
                tg_first_name = EXCLUDED.tg_first_name,
                tg_last_name = EXCLUDED.tg_last_name,
                tg_username = EXCLUDED.tg_username
            """,
            (self.tg_id, first_name, last_name, username),
        )

    # Профиль, который человек заполнил при регистрации (имя, телефон, адрес...).
    # Возвращает словарь или None, если пользователь ещё не зарегистрирован.
    async def get_profile(self):
        return await self.db.fetch_one(
            "SELECT * FROM users.users WHERE tg_id = %s", (self.tg_id,)
        )

    # True — если профиль есть, False — если нет.
    async def is_registered(self):
        return await self.get_profile() is not None

    # Сохраняем данные регистрации (вызывается в последнем шаге handlers/registration.py).
    async def register(self, first_name, last_name, phone, age, address):
        await self.db.execute(
            """
            INSERT INTO users.users (tg_id, first_name, last_name, phone, age, address)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (self.tg_id, first_name, last_name, phone, age, address),
        )

    # Кнопки главного меню для обычного пользователя.
    # Метод обычный (не async), т.к. к базе не обращается.
    def get_menu_buttons(self):
        return ["💊 Каталог", "🛒 Корзина", "📦 Мои заказы", "👤 Профиль"]


# НАСЛЕДОВАНИЕ (ООП): Admin(User) — администратор умеет всё то же, что и User,
# но метод get_menu_buttons ПЕРЕОПРЕДЕЛЁН (это полиморфизм):
# super().get_menu_buttons() — берём кнопки родителя (User) и добавляем "⚙️ Админ-панель".
class Admin(User):
    def get_menu_buttons(self):
        return super().get_menu_buttons() + ["⚙️ Админ-панель"]


# Проверка: является ли человек администратором.
# Вызывает SQL-функцию users.is_admin (sql/03_functions.sql).
async def is_admin(db, tg_id):
    row = await db.fetch_one("SELECT users.is_admin(%s) AS result", (tg_id,))
    # bool(...) — гарантирует ответ True/False, даже если row вдруг None.
    return bool(row and row["result"])


# "Фабрика": возвращает объект нужного класса — Admin или User.
# Обработчикам не нужно думать, кто перед ними: они просто вызывают
# user.get_menu_buttons(), и получают правильное меню (полиморфизм).
async def get_current_user(db, tg_id):
    if await is_admin(db, tg_id):
        return Admin(db, tg_id)
    return User(db, tg_id)


# Добавляет tg_id в users.tg_users, если его там ещё нет (DO NOTHING — если есть, ничего не делаем).
# Нужно в main.py перед выдачей прав админа: таблица users.admin ссылается на tg_users (внешний ключ).
async def ensure_tg_id_known(db, tg_id):
    await db.execute(
        "INSERT INTO users.tg_users (tg_id) VALUES (%s) ON CONFLICT (tg_id) DO NOTHING",
        (tg_id,),
    )


# Выдать права админа через SQL-функцию users.grant_admin. note — необязательная заметка.
async def grant_admin(db, tg_id, note=None):
    await db.execute("SELECT users.grant_admin(%s, %s)", (tg_id, note))


# ============================================================
#  КАТЕГОРИИ И ТОВАРЫ
# ============================================================

# Все категории, отсортированные по названию (для кнопок каталога).
async def get_categories(db):
    return await db.fetch_all("SELECT * FROM pharmacy.categories ORDER BY name")

# Добавить категорию. RETURNING * — сразу вернуть созданную строку (с её новым category_id).
async def add_category(db, name, description):
    return await db.fetch_one(
        "INSERT INTO pharmacy.categories (name, description) VALUES (%s, %s) RETURNING *",
        (name, description),
    )

# Товары одной категории.
# only_active=True  — только активные (для покупателей в каталоге);
# only_active=False — все, включая деактивированные (для админа).
async def get_products(db, category_id, only_active=True):
    if only_active:
        return await db.fetch_all(
            """
            SELECT * FROM pharmacy.products
            WHERE category_id = %s AND is_active = true
            ORDER BY name
            """,
            (category_id,),
        )
    return await db.fetch_all(
        "SELECT * FROM pharmacy.products WHERE category_id = %s ORDER BY name",
        (category_id,),
    )


# Один товар по его id (или None, если не найден).
async def get_product(db, product_id):
    return await db.fetch_one(
        "SELECT * FROM pharmacy.products WHERE product_id = %s", (product_id,)
    )

# Добавить новый товар (последний шаг диалога в handlers/admin.py).
async def add_product(db, category_id, name, manufacturer, description, price, stock, prescription):
    return await db.fetch_one(
        """
        INSERT INTO pharmacy.products
            (category_id, name, manufacturer, description, price, stock_quantity, requires_prescription)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (category_id, name, manufacturer, description, price, stock, prescription),
    )

# Изменить цену товара.
async def set_price(db, product_id, price):
    await db.execute(
        "UPDATE pharmacy.products SET price = %s WHERE product_id = %s", (price, product_id)
    )

# Изменить остаток на складе.
async def set_stock(db, product_id, stock):
    await db.execute(
        "UPDATE pharmacy.products SET stock_quantity = %s WHERE product_id = %s",
        (stock, product_id),
    )

# "Мягкое удаление": товар не удаляется из базы, а помечается is_active = false.
# Удалять нельзя, потому что он может быть в старых заказах
# (это запрещает триггер trg_protect_product_delete в sql/02_triggers.sql).
async def deactivate_product(db, product_id):
    await db.execute(
        "UPDATE pharmacy.products SET is_active = false WHERE product_id = %s", (product_id,)
    )

# ============================================================
#  ЗАКАЗЫ
# ============================================================

# Один заказ по номеру.
async def get_order(db, order_id):
    return await db.fetch_one("SELECT * FROM pharmacy.orders WHERE order_id = %s", (order_id,))

# Позиции (товары) заказа. price_at_order — цена на момент покупки,
# она не меняется, даже если админ потом изменит цену товара.
async def get_order_items(db, order_id):
    return await db.fetch_all(
        """
        SELECT oi.product_id, p.name AS product_name, oi.quantity, oi.price_at_order
        FROM pharmacy.order_items oi
        JOIN pharmacy.products p ON p.product_id = oi.product_id
        WHERE oi.order_id = %s
        """,
        (order_id,),
    )

# Заказы одного пользователя, новые сверху (DESC = по убыванию).
async def get_user_orders(db, user_id):
    return await db.fetch_all(
        "SELECT * FROM pharmacy.orders WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,),
    )

# Все заказы всех пользователей (для админа).
async def get_all_orders(db):
    return await db.fetch_all("SELECT * FROM pharmacy.orders ORDER BY created_at DESC")

# Сменить статус заказа. После этого UPDATE в базе автоматически срабатывают триггеры:
#   trg_log_order_change        — запись в журнал logs.audit_logs;
#   trg_restore_stock_on_cancel — при отмене товар возвращается на склад.
async def set_order_status(db, order_id, status):
    await db.execute(
        "UPDATE pharmacy.orders SET status = %s WHERE order_id = %s", (status, order_id)
    )

# Узнать Telegram-ID покупателя по номеру заказа (чтобы отправить ему уведомление).
# Цепочка: orders.user_id -> users.user_id -> users.tg_id
async def get_order_customer_tg_id(db, order_id):
    row = await db.fetch_one(
        """
        SELECT u.tg_id
        FROM pharmacy.orders o
        JOIN users.users u ON u.user_id = o.user_id
        WHERE o.order_id = %s
        """,
        (order_id,),
    )
    return row["tg_id"] if row else None

# Список Telegram-ID всех админов (им отправляется уведомление о новом заказе).
async def get_admin_tg_ids(db):
    rows = await db.fetch_all("SELECT tg_id FROM users.admin")
    # Из списка словарей делаем список чисел: [{"tg_id": 1}, {"tg_id": 2}] -> [1, 2]
    return [row["tg_id"] for row in rows]

# ============================================================
#  СТАТИСТИКА И ЛОГИ
# ============================================================

# Выручка за период — через SQL-функцию pharmacy.get_revenue.
async def get_revenue(db, date_from, date_to):
    row = await db.fetch_one(
        "SELECT pharmacy.get_revenue(%s, %s) AS revenue", (date_from, date_to)
    )
    return row["revenue"]

# Самые продаваемые товары. SQL-функция возвращает таблицу, поэтому SELECT * FROM функция(...).
async def get_top_products(db, limit=5):
    return await db.fetch_all("SELECT * FROM pharmacy.get_top_products(%s)", (limit,))

# Записать действие пользователя в журнал logs.bot_logs (например, "checkout").
async def log_action(db, tg_id, action, details=None):
    await db.execute(
        "INSERT INTO logs.bot_logs (tg_id, action, details) VALUES (%s, %s, %s)",
        (tg_id, action, details),
    )

# Удалить старые логи (старше older_than_days дней). Возвращает количество удалённых записей.
# Сейчас в боте нигде не вызывается — задел на будущее / для ручного запуска.
async def cleanup_old_logs(db, older_than_days=90):
    row = await db.fetch_one("SELECT logs.cleanup_old_logs(%s) AS deleted", (older_than_days,))
    return row["deleted"]
