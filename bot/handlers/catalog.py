"""
handlers/catalog.py — КАТАЛОГ ТОВАРОВ.

Путь пользователя:
    "💊 Каталог" -> список категорий -> список товаров -> карточка товара
    -> выбор количества ➖/➕ -> "🛒 В корзину"

Здесь почти всё работает через inline-кнопки (CallbackQuery), и вместо
отправки новых сообщений бот РЕДАКТИРУЕТ одно и то же сообщение
(safe_edit_text / safe_edit_markup из utils.py) — так чат не засоряется.

Какой callback_data какую функцию вызывает (кнопки созданы в keyboards.py):
    "categories"             -> back_to_categories
    "cat:<id>"               -> show_products
    "product:<id>"           -> show_product_detail
    "product_qty:<id>:<n>"   -> change_product_quantity
    "add_to_cart:<id>:<n>"   -> add_to_cart
    "noop"                   -> noop (ничего не делает)
"""

from aiogram import F, Router
# CallbackQuery — событие "нажата inline-кнопка". В нём есть .data (строка callback_data)
# и .message (сообщение, под которым была кнопка).
from aiogram.types import CallbackQuery, Message

from bot import keyboards
from bot import models
from bot.models import Order, get_current_user
from bot.utils import esc, safe_edit_markup, safe_edit_text

router = Router(name="catalog")


# Собирает текст карточки товара. product — словарь-строка из pharmacy.products.
def format_product(product):
    lines = [
        f"<b>{esc(product['name'])}</b>",
        # product['manufacturer'] or '—' — если производителя нет (None), показать прочерк.
        f"Производитель: {esc(product['manufacturer'] or '—')}",
        esc(product["description"] or ""),
        f"Цена: {product['price']} SUM",
        f"В наличии: {product['stock_quantity']} шт.",
    ]
    if product["requires_prescription"]:
        lines.append("⚠️ Отпускается по рецепту")
    # Генератор с условием "if line" — пропускаем пустые строки (например, пустое описание).
    return "\n".join(line for line in lines if line)


# Кнопка меню "💊 Каталог" — отправляем НОВОЕ сообщение со списком категорий.
@router.message(F.text == "💊 Каталог")
async def show_categories(message: Message, db) -> None:
    categories = await models.get_categories(db)
    await message.answer("Выберите категорию:", reply_markup=keyboards.categories_keyboard(categories))


# @router.callback_query — обработчик нажатий INLINE-кнопок (а не текстовых сообщений).
# F.data == "categories" — точное совпадение callback_data.
# Кнопка "Назад" — возвращаемся к списку категорий, редактируя текущее сообщение.
@router.callback_query(F.data == "categories")
async def back_to_categories(callback: CallbackQuery, db) -> None:
    categories = await models.get_categories(db)
    await safe_edit_text(
        callback.message, "Выберите категорию:", keyboards.categories_keyboard(categories)
    )
    # callback.answer() — ОБЯЗАТЕЛЬНО ответить Telegram на нажатие,
    # иначе на кнопке будут долго крутиться "часики" загрузки.
    await callback.answer()


# F.data.startswith("cat:") — callback_data НАЧИНАЕТСЯ с "cat:" (например "cat:3").
@router.callback_query(F.data.startswith("cat:"))
async def show_products(callback: CallbackQuery, db) -> None:
    # "cat:3".split(":") -> ["cat", "3"]; [1] -> "3"; int(...) -> 3
    category_id = int(callback.data.split(":")[1])
    products = await models.get_products(db, category_id)   # только активные товары
    if not products:
        # show_alert=True — показать всплывающее окно, а не маленькую подсказку сверху.
        await callback.answer("В этой категории пока нет товаров", show_alert=True)
        return
    await safe_edit_text(
        callback.message, "Товары в категории:", keyboards.products_keyboard(products)
    )
    await callback.answer()


# Нажали на товар — показываем карточку товара с кнопками количества (по умолчанию 1 шт.).
@router.callback_query(F.data.startswith("product:"))
async def show_product_detail(callback: CallbackQuery, db) -> None:
    product_id = int(callback.data.split(":")[1])
    product = await models.get_product(db, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await safe_edit_text(
        callback.message, format_product(product), keyboards.product_keyboard(product_id)
    )
    await callback.answer()


# Кнопки ➖ / ➕ в карточке товара. callback_data: "product_qty:<id товара>:<новое кол-во>".
@router.callback_query(F.data.startswith("product_qty:"))
async def change_product_quantity(callback: CallbackQuery, db) -> None:
    # Распаковка: "product_qty:5:2".split(":") -> ["product_qty", "5", "2"].
    # "_" — переменная-"мусорка" для значения, которое нам не нужно (слово "product_qty").
    _, product_id_raw, quantity_raw = callback.data.split(":")
    # Множественное присваивание: две переменные за одну строку.
    product_id, quantity = int(product_id_raw), int(quantity_raw)

    if quantity < 1:
        await callback.answer("Меньше 1 шт. нельзя")
        return

    product = await models.get_product(db, product_id)
    if product is None or not product["is_active"]:
        await callback.answer("Товар недоступен", show_alert=True)
        return
    if quantity > product["stock_quantity"]:
        await callback.answer(f"В наличии только {product['stock_quantity']} шт.")
        return

    # Меняем только кнопки (текст карточки остаётся) — теперь в них новое количество.
    await safe_edit_markup(callback.message, keyboards.product_keyboard(product_id, quantity))
    await callback.answer()


# Кнопка "🛒 В корзину (N шт.)". callback_data: "add_to_cart:<id>:<кол-во>".
@router.callback_query(F.data.startswith("add_to_cart:"))
async def add_to_cart(callback: CallbackQuery, db) -> None:
    parts = callback.data.split(":")
    product_id = int(parts[1])
    # Если количества в callback_data нет (старая кнопка), берём 1.
    quantity = int(parts[2]) if len(parts) > 2 else 1
    tg_user = callback.from_user

    # Покупать могут только зарегистрированные пользователи.
    user = await get_current_user(db, tg_user.id)
    profile = await user.get_profile()
    if profile is None:
        await callback.answer("Сначала завершите регистрацию: /start", show_alert=True)
        return

    # Создаём объект корзины (models.Order) и добавляем товар.
    order = Order(db, profile["user_id"])
    # add_item возвращает два значения — сразу раскладываем в ok и error.
    ok, error = await order.add_item(product_id, quantity=quantity)
    if not ok:
        await callback.answer(error, show_alert=True)
        return

    await callback.answer(f"Добавлено в корзину: {quantity} шт. ✅")


# Кнопки-надписи ("3 шт.", название товара в корзине) ничего не делают —
# просто отвечаем Telegram, чтобы убрать "часики" загрузки.
@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
