"""
handlers/cart.py — КОРЗИНА И ОФОРМЛЕНИЕ ЗАКАЗА.

Путь пользователя:
    "🛒 Корзина" -> изменить количество ➖/➕, удалить ❌, очистить 🗑
    -> "✅ Оформить заказ" -> ввести адрес -> заказ создан
    -> всем админам приходит уведомление с кнопками смены статуса

Связи:
    models.Order          — все действия с корзиной и checkout()
    keyboards.cart_keyboard — кнопки корзины
    states.CheckoutStates — состояние "ждём адрес доставки"
    SQL pharmacy.create_order (sql/03_functions.sql) — создание заказа в базе
"""

# psycopg — нужен для перехвата ошибки psycopg.errors.RaiseException из SQL-функции.
import psycopg

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards
from bot import models
from bot.models import Order, get_current_user
from bot.states import CheckoutStates
from bot.utils import esc, safe_edit_text

router = Router(name="cart")


# Собирает текст корзины и кнопки к нему.
# Возвращает ДВА значения (кортеж): (текст, клавиатура). Если корзина пуста — (текст, None).
# order: Order — подсказка типа: сюда передают объект класса Order.
async def render_cart(order: Order):
    items = await order.get_items()
    if not items:
        return "Корзина пуста.", None

    lines = ["<b>Ваша корзина</b>"]
    for item in items:
        subtotal = item["price"] * item["quantity"]   # сумма по одной позиции
        lines.append(
            f"{esc(item['product_name'])} — {item['quantity']} шт. x {item['price']} SUM = {subtotal} SUM"
        )
    total = await order.get_total()
    lines.append(f"\nИтого: {total} SUM")
    return "\n".join(lines), keyboards.cart_keyboard(items)


# Кнопка меню "🛒 Корзина".
@router.message(F.text == "🛒 Корзина")
async def show_cart(message: Message, db) -> None:
    user = await get_current_user(db, message.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await message.answer("Сначала завершите регистрацию: /start")
        return

    # profile["user_id"] — внутренний id из таблицы users.users (не Telegram-ID).
    order = Order(db, profile["user_id"])
    text, markup = await render_cart(order)   # распаковываем кортеж из двух значений
    await message.answer(text, reply_markup=markup)


# Кнопки ➖ / ➕ в корзине. callback_data: "cart_qty:<id товара>:<новое кол-во>".
@router.callback_query(F.data.startswith("cart_qty:"))
async def change_cart_quantity(callback: CallbackQuery, db) -> None:
    user = await get_current_user(db, callback.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await callback.answer("Сначала завершите регистрацию: /start", show_alert=True)
        return

    _, product_id_raw, quantity_raw = callback.data.split(":")
    order = Order(db, profile["user_id"])
    # Если новое количество 0 — set_item_quantity сам удалит товар из корзины.
    ok, error = await order.set_item_quantity(int(product_id_raw), int(quantity_raw))
    if not ok:
        await callback.answer(error, show_alert=True)
        return

    # Перерисовываем корзину в том же сообщении.
    text, markup = await render_cart(order)
    await safe_edit_text(callback.message, text, markup)
    await callback.answer()


# Кнопка ❌ — удалить товар. callback_data: "remove_from_cart:<id товара>".
@router.callback_query(F.data.startswith("remove_from_cart:"))
async def remove_from_cart(callback: CallbackQuery, db) -> None:
    user = await get_current_user(db, callback.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await callback.answer("Сначала завершите регистрацию: /start", show_alert=True)
        return

    product_id = int(callback.data.split(":")[1])
    order = Order(db, profile["user_id"])
    await order.remove_item(product_id)

    text, markup = await render_cart(order)
    await safe_edit_text(callback.message, text, markup)
    await callback.answer("Удалено из корзины")


# Кнопка "🗑 Очистить корзину".
@router.callback_query(F.data == "clear_cart")
async def clear_cart(callback: CallbackQuery, db) -> None:
    user = await get_current_user(db, callback.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await callback.answer("Сначала завершите регистрацию: /start", show_alert=True)
        return

    order = Order(db, profile["user_id"])
    await order.clear()
    await safe_edit_text(callback.message, "Корзина очищена.")
    await callback.answer()


# Кнопка "✅ Оформить заказ" — ШАГ 1: спрашиваем адрес доставки.
@router.callback_query(F.data == "checkout")
async def start_checkout(callback: CallbackQuery, state: FSMContext, db) -> None:
    user = await get_current_user(db, callback.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await callback.answer("Сначала завершите регистрацию: /start", show_alert=True)
        return

    order = Order(db, profile["user_id"])
    items = await order.get_items()
    if not items:
        await callback.answer("Корзина пуста", show_alert=True)
        return

    # Переводим пользователя в состояние "ждём адрес".
    # Следующее его сообщение поймает finish_checkout (ниже).
    await state.set_state(CheckoutStates.waiting_address)
    await callback.message.answer(
        "Введите адрес доставки или нажмите кнопку ниже, чтобы использовать адрес из профиля:\n"
        f"({esc(profile['address'])})",
        reply_markup=keyboards.profile_address_keyboard(),
    )
    await callback.answer()


# ШАГ 2: пользователь прислал адрес — создаём заказ.
@router.message(CheckoutStates.waiting_address)
async def finish_checkout(message: Message, state: FSMContext, db) -> None:
    tg_user = message.from_user
    user = await get_current_user(db, tg_user.id)
    profile = await user.get_profile()
    if profile is None:
        await state.clear()
        await message.answer("Сначала завершите регистрацию: /start")
        return

    address = (message.text or "").strip()
    # Если нажали кнопку "📍 Адрес из профиля" (или ввели "=") — берём адрес из профиля.
    # "x in (a, b)" — проверка, равно ли x одному из значений.
    if address in ("=", keyboards.PROFILE_ADDRESS_BUTTON):
        address = profile["address"]
    if not address:
        await message.answer("Адрес не может быть пустым. Введите адрес доставки:")
        return
    if len(address) > 255:
        await message.answer("Слишком длинный адрес (максимум 255 символов). Введите адрес доставки:")
        return

    # Главное меню — вернём его пользователю после оформления.
    menu = keyboards.main_keyboard(user.get_menu_buttons())

    order = Order(db, profile["user_id"])
    try:
        # Вызывает SQL-функцию pharmacy.create_order (sql/03_functions.sql).
        order_id = await order.checkout(address)
    # Если пока человек оформлял, товар закончился — SQL-функция делает RAISE EXCEPTION,
    # а в Python это приходит как psycopg.errors.RaiseException.
    # "as e" — сохраняем объект ошибки в переменную e.
    except psycopg.errors.RaiseException as e:
        await state.clear()
        # e.diag.message_primary — текст ошибки из SQL ("товара «...» уже не хватает...").
        reason = e.diag.message_primary or "недостаточно товара на складе"
        await message.answer(
            f"😔 Не получилось оформить заказ: {reason}\n"
            "Откройте корзину и уменьшите количество или уберите позицию.",
            reply_markup=menu,
        )
        return
    if order_id is None:
        await state.clear()
        await message.answer("Корзина пуста, оформлять нечего.", reply_markup=menu)
        return

    # Заказ создан — выходим из состояния оформления.
    await state.clear()
    # Перечитываем заказ из базы, чтобы получить итоговую сумму
    # (её посчитал триггер trg_recalc_order_total).
    new_order = await models.get_order(db, order_id)
    await message.answer(
        f"✅ Заказ #{order_id} принят на сумму {new_order['total_amount']} SUM.\n"
        "Ожидайте: аптека подтвердит заказ и сообщит, когда он будет готов.",
        reply_markup=menu,
    )
    # Пишем в журнал logs.bot_logs, что пользователь оформил заказ.
    await models.log_action(db, tg_user.id, "checkout", f"order_id={order_id}")

    # Сообщаем всем админам о новом заказе.
    await notify_admins_about_order(message, db, order_id, profile)


# Отправляет каждому админу сообщение о новом заказе + кнопки смены статуса
# (их нажатия обрабатывает handlers/admin.py -> admin_change_order_status).
async def notify_admins_about_order(message: Message, db, order_id: int, profile) -> None:
    order = await models.get_order(db, order_id)
    items = await models.get_order_items(db, order_id)

    lines = [
        f"🔔 <b>Новый заказ #{order_id}</b>",
        f"Покупатель: {esc(profile['first_name'])} {esc(profile['last_name'])}, "
        f"тел. {esc(profile['phone'])}",
        f"Адрес: {esc(order['delivery_address'])}",
        "",
    ]
    for item in items:
        lines.append(
            f"• {esc(item['product_name'])} — {item['quantity']} шт. x {item['price_at_order']} SUM"
        )
    lines.append(f"\nИтого: {order['total_amount']} SUM")
    text = "\n".join(lines)

    for admin_tg_id in await models.get_admin_tg_ids(db):
        try:
            # message.bot — объект бота; send_message(кому, текст) — написать конкретному человеку.
            await message.bot.send_message(
                admin_tg_id, text, reply_markup=keyboards.order_status_keyboard(order_id)
            )
        # Если админ заблокировал бота или ни разу ему не писал, Telegram вернёт ошибку.
        # Игнорируем её (pass = "ничего не делать"), чтобы остальные админы всё равно получили сообщение.
        except Exception:
            pass
