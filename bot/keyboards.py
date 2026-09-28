"""
keyboards.py — ВСЕ КНОПКИ БОТА.

В Telegram есть два вида кнопок:

1) Reply-клавиатура (ReplyKeyboardBuilder) — кнопки ВМЕСТО обычной клавиатуры телефона.
   При нажатии бот получает ОБЫЧНОЕ СООБЩЕНИЕ с текстом кнопки.
   Поэтому обработчики ловят их так: F.text == "🛒 Корзина".

2) Inline-клавиатура (InlineKeyboardBuilder) — кнопки ПОД сообщением.
   При нажатии бот получает CallbackQuery со скрытой строкой callback_data.
   Обработчики ловят их так: F.data == "checkout" или F.data.startswith("cat:").

Формат callback_data — "команда:параметр:параметр", например "cart_qty:5:3"
(товар №5, количество 3). В обработчике строку режут через .split(":").
Ограничение Telegram: callback_data не длиннее 64 байт.
"""

# InlineKeyboardButton — одна inline-кнопка (нужна, когда кнопки собираем вручную рядами).
from aiogram.types import InlineKeyboardButton
# "Строители" клавиатур: добавляем кнопки по одной, потом .as_markup() даёт готовую клавиатуру.
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# Тексты кнопок вынесены в константы (ЗАГЛАВНЫЕ буквы = константа),
# потому что их же проверяют в handlers/admin.py и handlers/cart.py.
# Если поменять текст здесь — он поменяется везде.
SKIP_BUTTON = "➡️ Пропустить"
PROFILE_ADDRESS_BUTTON = "📍 Адрес из профиля"


# Главное меню. buttons — список текстов из User.get_menu_buttons() / Admin.get_menu_buttons().
def main_keyboard(buttons):
    builder = ReplyKeyboardBuilder()
    for text in buttons:
        builder.button(text=text)
    # adjust(2) — расставить кнопки по 2 в ряд.
    builder.adjust(2)
    # resize_keyboard=True — кнопки подгоняются по размеру (иначе они огромные).
    return builder.as_markup(resize_keyboard=True)


# Кнопка "Отправить номер телефона" (при регистрации).
# request_contact=True — Telegram сам отправит номер пользователя (после его подтверждения).
# one_time_keyboard=True — клавиатура скроется после нажатия.
def phone_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📞 Отправить номер телефона", request_contact=True)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


# Кнопка "Пропустить" — для необязательных полей (описание, производитель).
def skip_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text=SKIP_BUTTON)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


# Кнопка "Адрес из профиля" при оформлении заказа.
def profile_address_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text=PROFILE_ADDRESS_BUTTON)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


# Кнопки "Да" / "Нет" (вопрос "Требуется рецепт?" при добавлении товара).
def yes_no_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="Да")
    builder.button(text="Нет")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


# ---------------- Inline-кнопки для покупателя ----------------

# Список категорий. Нажатие -> callback "cat:<id>" -> handlers/catalog.py -> show_products
def categories_keyboard(categories):
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category["name"], callback_data=f"cat:{category['category_id']}")
    builder.adjust(1)   # по одной кнопке в ряд
    return builder.as_markup()


# Список товаров категории. Нажатие -> "product:<id>" -> catalog.py -> show_product_detail
# Кнопка "Назад" -> "categories" -> catalog.py -> back_to_categories
def products_keyboard(products):
    builder = InlineKeyboardBuilder()
    for product in products:
        text = f"{product['name']} — {product['price']} SUM"
        builder.button(text=text, callback_data=f"product:{product['product_id']}")
    builder.button(text="⬅️ К категориям", callback_data="categories")
    builder.adjust(1)
    return builder.as_markup()


# Карточка товара: [➖] [N шт.] [➕], ниже "В корзину", ниже "Назад".
# Текущее количество "зашито" прямо в кнопки: ➖ отправляет quantity-1, ➕ отправляет quantity+1.
# Так боту не нужно нигде хранить, какое число сейчас выбрано.
# builder.row(...) — добавить целый ряд кнопок сразу.
def product_keyboard(product_id, quantity=1):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➖", callback_data=f"product_qty:{product_id}:{quantity - 1}"),
        # "noop" (no operation) — кнопка-надпись, нажатие ничего не делает.
        InlineKeyboardButton(text=f"{quantity} шт.", callback_data="noop"),
        InlineKeyboardButton(text="➕", callback_data=f"product_qty:{product_id}:{quantity + 1}"),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🛒 В корзину ({quantity} шт.)",
            callback_data=f"add_to_cart:{product_id}:{quantity}",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="categories"))
    return builder.as_markup()


# Корзина: для каждого товара — строка с названием и ряд [➖][N шт.][➕][❌],
# внизу — "Оформить заказ" и "Очистить корзину". Все callback'и ловит handlers/cart.py.
# items — результат Order.get_items() из models.py.
def cart_keyboard(items):
    builder = InlineKeyboardBuilder()
    for item in items:
        product_id = item["product_id"]
        quantity = item["quantity"]
        builder.row(InlineKeyboardButton(text=item["product_name"], callback_data="noop"))
        builder.row(
            InlineKeyboardButton(text="➖", callback_data=f"cart_qty:{product_id}:{quantity - 1}"),
            InlineKeyboardButton(text=f"{quantity} шт.", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_qty:{product_id}:{quantity + 1}"),
            InlineKeyboardButton(text="❌", callback_data=f"remove_from_cart:{product_id}"),
        )
    builder.row(InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"))
    builder.row(InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart"))
    return builder.as_markup()


# ---------------- Inline-кнопки для администратора ----------------
# Все callback'и с префиксом "admin..." ловит handlers/admin.py.

# Меню админ-панели.
def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить товар", callback_data="admin:add_product")
    builder.button(text="📂 Добавить категорию", callback_data="admin:add_category")
    builder.button(text="✏️ Изменить цену/остаток", callback_data="admin:edit_product")
    builder.button(text="🚫 Деактивировать товар", callback_data="admin:deactivate_product")
    builder.button(text="📦 Все заказы", callback_data="admin:orders")
    builder.button(text="📊 Статистика", callback_data="admin:stats")
    builder.adjust(1)
    return builder.as_markup()


# Выбор категории админом. action говорит, ЗАЧЕМ выбирают категорию:
# "add_product", "edit" или "deactivate". Он попадает в callback_data: "admin_cat:edit:3".
# Так одна функция обслуживает три разных сценария.
def admin_categories_keyboard(categories, action):
    builder = InlineKeyboardBuilder()
    for category in categories:
        callback_data = f"admin_cat:{action}:{category['category_id']}"
        builder.button(text=category["name"], callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()


# Выбор товара админом (для "edit" или "deactivate"): "admin_product:edit:12".
def admin_products_keyboard(products, action):
    builder = InlineKeyboardBuilder()
    for product in products:
        callback_data = f"admin_product:{action}:{product['product_id']}"
        builder.button(text=product["name"], callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()


# Что редактировать у товара: цену или остаток. "admin_field:price:12"
def admin_edit_field_keyboard(product_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Цена", callback_data=f"admin_field:price:{product_id}")
    builder.button(text="📦 Остаток", callback_data=f"admin_field:stock:{product_id}")
    builder.adjust(2)
    return builder.as_markup()


# Кнопки смены статуса заказа (приходят админу вместе с новым заказом).
# transitions — список пар (статус в базе, текст на кнопке).
# Нажатие -> "order_status:<номер>:<статус>" -> admin.py -> admin_change_order_status
def order_status_keyboard(order_id):
    builder = InlineKeyboardBuilder()
    transitions = [
        ("confirmed", "Подтвердить"),
        ("ready", "Готов к выдаче"),
        ("completed", "Завершить"),
        ("cancelled", "Отменить"),
    ]
    # Распаковка кортежа: каждая пара сразу раскладывается в две переменные status и label.
    for status, label in transitions:
        builder.button(text=label, callback_data=f"order_status:{order_id}:{status}")
    builder.adjust(2)
    return builder.as_markup()
