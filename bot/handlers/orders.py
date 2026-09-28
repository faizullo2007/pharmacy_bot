"""
handlers/orders.py — КНОПКА "📦 Мои заказы": история заказов покупателя.

Связи:
    models.get_user_orders  — список заказов пользователя
    models.get_order_items  — товары внутри заказа
"""

# F — "магический фильтр" aiogram. F.text == "..." означает "текст сообщения равен ...".
from aiogram import F, Router
from aiogram.types import Message

from bot import models
from bot.models import get_current_user
from bot.utils import esc

router = Router(name="orders")

# Словарь: статус в базе (английский) -> понятный текст для пользователя (русский).
STATUS_LABELS = {
    "pending": "Ожидает подтверждения",
    "confirmed": "Подтверждён",
    "ready": "Готов к выдаче",
    "completed": "Завершён",
    "cancelled": "Отменён",
}


# Собирает текст одного заказа для отправки в Telegram.
# order — словарь-строка из таблицы pharmacy.orders.
async def format_order(db, order):
    items = await models.get_order_items(db, order["order_id"])
    lines = [
        # {order['created_at']:%d.%m.%Y %H:%M} — форматирование даты прямо в f-строке:
        # %d день, %m месяц, %Y год, %H часы, %M минуты -> "28.09.2026 14:30".
        f"<b>Заказ #{order['order_id']}</b> от {order['created_at']:%d.%m.%Y %H:%M}",
        # .get(ключ, запасное) — если статуса нет в словаре, покажем его как есть.
        f"Статус: {STATUS_LABELS.get(order['status'], order['status'])}",
    ]
    for item in items:
        subtotal = item["price_at_order"] * item["quantity"]
        lines.append(f"  • {esc(item['product_name'])} x{item['quantity']} = {subtotal} SUM")
    lines.append(f"Итого: {order['total_amount']} SUM")
    # "\n".join(lines) — склеить все строки списка через перенос строки.
    return "\n".join(lines)


# Срабатывает, когда пользователь нажал кнопку меню "📦 Мои заказы"
# (reply-кнопка присылает обычное сообщение с этим текстом).
# db — aiogram подставляет его сам, потому что в main.py было dp["db"] = db.
@router.message(F.text == "📦 Мои заказы")
async def show_my_orders(message: Message, db) -> None:
    # message.from_user.id — Telegram-ID того, кто написал.
    user = await get_current_user(db, message.from_user.id)
    profile = await user.get_profile()
    if profile is None:
        await message.answer("Сначала завершите регистрацию: /start")
        return   # return — выходим из функции, дальше код не выполняется

    orders = await models.get_user_orders(db, profile["user_id"])
    if not orders:
        await message.answer("У вас пока нет заказов.")
        return

    # orders[:10] — срез: только первые 10 (самые новые) заказов, каждый отдельным сообщением.
    for order in orders[:10]:
        await message.answer(await format_order(db, order))
