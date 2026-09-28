"""
handlers/admin.py — АДМИН-ПАНЕЛЬ.

Все обработчики этого файла работают ТОЛЬКО для администраторов:
это обеспечивает фильтр admin_only, повешенный на весь роутер сразу.

Возможности админа (кнопки из keyboards.admin_panel_keyboard):
    ➕ Добавить товар        — пошаговый диалог (AddProductStates)
    📂 Добавить категорию    — пошаговый диалог (AddCategoryStates)
    ✏️ Изменить цену/остаток — категория -> товар -> поле -> новое значение (EditProductStates)
    🚫 Деактивировать товар  — категория -> товар -> is_active = false
    📦 Все заказы            — список заказов с кнопками смены статуса
    📊 Статистика            — выручка за 30 дней и топ-5 товаров
"""

# datetime — дата и время; timedelta — промежуток времени (например, 30 дней);
# timezone.utc — часовой пояс UTC (в базе время хранится с часовым поясом, TIMESTAMPTZ).
from datetime import datetime, timedelta, timezone
# Decimal — точные десятичные числа. Для денег НЕЛЬЗЯ использовать float:
# 0.1 + 0.2 во float = 0.30000000000000004. Decimal считает точно.
# InvalidOperation — ошибка, если строку нельзя превратить в Decimal (например, "abc").
from decimal import Decimal, InvalidOperation

import psycopg

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot import keyboards
from bot import models
from bot.states import AddCategoryStates, AddProductStates, EditProductStates
from bot.utils import esc

# Максимальная цена: NUMERIC(10,2) в таблице = 10 цифр, из них 2 после запятой -> 99999999.99
MAX_PRICE = Decimal("99999999.99")
# Максимальный остаток на складе. 1_000_000 — то же, что 1000000 (подчёркивания для читаемости).
MAX_STOCK = 1_000_000


# Превращает введённый текст в цену. Возвращает Decimal или None, если ввод неправильный.
def parse_price(raw):
    try:
        # .replace(",", ".") — чтобы "199,90" тоже работало.
        price = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    # is_finite() — отсекаем "бесконечность" и "NaN" (Decimal их тоже понимает).
    if not price.is_finite() or price < 0 or price > MAX_PRICE:
        return None
    # quantize(Decimal("0.01")) — округлить до 2 знаков после запятой (копейки/тийины).
    return price.quantize(Decimal("0.01"))


router = Router(name="admin")


# ФИЛЬТР ДОСТУПА. event — сообщение или нажатие кнопки; у обоих есть from_user.
# Возвращает True (пропустить) или False (этот роутер пропускает событие дальше).
async def admin_only(event, db) -> bool:
    return await models.is_admin(db, event.from_user.id)


# Вешаем фильтр на ВЕСЬ роутер: и на сообщения, и на нажатия кнопок.
# Поэтому в каждом обработчике ниже не нужно отдельно проверять, админ ли это.
# Если пишет не админ — aiogram идёт к следующим роутерам (catalog, cart, ...).
router.message.filter(admin_only)
router.callback_query.filter(admin_only)


# Кнопка меню "⚙️ Админ-панель" (есть только у Admin, см. models.Admin.get_menu_buttons).
@router.message(F.text == "⚙️ Админ-панель")
async def open_admin_panel(message: Message) -> None:
    await message.answer("Админ-панель:", reply_markup=keyboards.admin_panel_keyboard())


# ================= ДОБАВЛЕНИЕ КАТЕГОРИИ =================

# Шаг 0: нажата кнопка "📂 Добавить категорию".
@router.callback_query(F.data == "admin:add_category")
async def admin_add_category_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddCategoryStates.waiting_name)
    await callback.message.answer("Введите название новой категории:")
    await callback.answer()


# Шаг 1: название категории.
@router.message(AddCategoryStates.waiting_name)
async def admin_add_category_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым:")
        return
    if len(name) > 100:   # как VARCHAR(100) в pharmacy.categories
        await message.answer("Слишком длинное название (максимум 100 символов):")
        return
    await state.update_data(name=name)
    await state.set_state(AddCategoryStates.waiting_description)
    await message.answer(
        "Введите описание категории или нажмите кнопку ниже:",
        reply_markup=keyboards.skip_keyboard(),
    )


# Шаг 2: описание и сохранение в базу.
@router.message(AddCategoryStates.waiting_description)
async def admin_add_category_description(message: Message, state: FSMContext, db) -> None:
    raw = (message.text or "").strip()
    # Если нажали "Пропустить" (или ввели "-") — описания нет (None -> NULL в базе).
    description = None if raw in ("-", keyboards.SKIP_BUTTON) else raw
    data = await state.get_data()
    await state.clear()

    user = await models.get_current_user(db, message.from_user.id)
    menu = keyboards.main_keyboard(user.get_menu_buttons())
    try:
        category = await models.add_category(db, data["name"], description)
    # UniqueViolation — нарушение UNIQUE: категория с таким названием уже есть.
    except psycopg.errors.UniqueViolation:
        await message.answer(
            f"Категория «{esc(data['name'])}» уже существует.", reply_markup=menu
        )
        return

    await message.answer(f"✅ Категория «{esc(category['name'])}» создана.", reply_markup=menu)


# ================= ДОБАВЛЕНИЕ ТОВАРА =================

# Шаг 0: нажата кнопка "➕ Добавить товар" — показываем категории.
@router.callback_query(F.data == "admin:add_product")
async def admin_add_product_start(callback: CallbackQuery, state: FSMContext, db) -> None:
    categories = await models.get_categories(db)
    if not categories:
        await callback.answer("Сначала добавьте хотя бы одну категорию", show_alert=True)
        return
    await state.set_state(AddProductStates.waiting_category)
    await callback.message.answer(
        "Выберите категорию для нового товара:",
        reply_markup=keyboards.admin_categories_keyboard(categories, action="add_product"),
    )
    await callback.answer()


# Шаг 1: выбрана категория. Два фильтра: callback начинается с "admin_cat:add_product:"
# И пользователь находится в состоянии waiting_category.
@router.callback_query(F.data.startswith("admin_cat:add_product:"), AddProductStates.waiting_category)
async def admin_add_product_category(callback: CallbackQuery, state: FSMContext) -> None:
    # "admin_cat:add_product:3".split(":") -> ["admin_cat", "add_product", "3"]; [2] -> "3"
    category_id = int(callback.data.split(":")[2])
    await state.update_data(category_id=category_id)
    await state.set_state(AddProductStates.waiting_name)
    await callback.message.answer("Введите название товара:")
    await callback.answer()


# Шаг 2: название товара.
@router.message(AddProductStates.waiting_name)
async def admin_add_product_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым:")
        return
    if len(name) > 200:
        await message.answer("Слишком длинное название (максимум 200 символов):")
        return
    await state.update_data(name=name)
    await state.set_state(AddProductStates.waiting_manufacturer)
    await message.answer(
        "Введите производителя или нажмите кнопку ниже:",
        reply_markup=keyboards.skip_keyboard(),
    )


# Шаг 3: производитель (необязательно).
@router.message(AddProductStates.waiting_manufacturer)
async def admin_add_product_manufacturer(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw not in ("-", keyboards.SKIP_BUTTON) and len(raw) > 150:
        await message.answer("Слишком длинно (максимум 150 символов). Введите производителя:")
        return
    await state.update_data(manufacturer=None if raw in ("-", keyboards.SKIP_BUTTON) else raw)
    await state.set_state(AddProductStates.waiting_description)
    await message.answer(
        "Введите описание товара или нажмите кнопку ниже:",
        reply_markup=keyboards.skip_keyboard(),
    )


# Шаг 4: описание (необязательно, TEXT — без ограничения длины).
@router.message(AddProductStates.waiting_description)
async def admin_add_product_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(description=None if raw in ("-", keyboards.SKIP_BUTTON) else raw)
    await state.set_state(AddProductStates.waiting_price)
    # ReplyKeyboardRemove — убираем кнопку "Пропустить": цену пропустить нельзя.
    await message.answer("Введите цену (например, 199.90):", reply_markup=ReplyKeyboardRemove())


# Шаг 5: цена.
@router.message(AddProductStates.waiting_price)
async def admin_add_product_price(message: Message, state: FSMContext) -> None:
    price = parse_price((message.text or "").strip())
    if price is None:
        await message.answer(f"Введите цену числом от 0 до {MAX_PRICE}, например 199.90:")
        return
    # Сохраняем как строку str(price): хранилище FSM рассчитано на простые типы,
    # а при сохранении в базу превратим обратно в Decimal.
    await state.update_data(price=str(price))
    await state.set_state(AddProductStates.waiting_stock)
    await message.answer("Введите количество на складе:")


# Шаг 6: остаток на складе.
@router.message(AddProductStates.waiting_stock)
async def admin_add_product_stock(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdecimal() or int(raw) > MAX_STOCK:
        await message.answer(f"Введите количество целым числом от 0 до {MAX_STOCK}:")
        return
    await state.update_data(stock_quantity=int(raw))
    await state.set_state(AddProductStates.waiting_prescription_flag)
    await message.answer("Требуется рецепт?", reply_markup=keyboards.yes_no_keyboard())


# Шаг 7 (последний): нужен ли рецепт -> сохраняем товар в базу.
@router.message(AddProductStates.waiting_prescription_flag)
async def admin_add_product_prescription(message: Message, state: FSMContext, db) -> None:
    # .lower() — в нижний регистр, чтобы "Да", "ДА" и "да" считались одинаково.
    raw = (message.text or "").strip().lower()
    if raw not in ("да", "нет"):
        await message.answer("Ответьте «да» или «нет»:")
        return

    data = await state.get_data()   # всё, что накопили на шагах 1–6
    product = await models.add_product(
        db,
        category_id=data["category_id"],
        name=data["name"],
        manufacturer=data["manufacturer"],
        description=data["description"],
        price=Decimal(data["price"]),       # строку обратно в Decimal
        stock=data["stock_quantity"],
        prescription=(raw == "да"),         # сравнение даёт True или False
    )
    await state.clear()

    user = await models.get_current_user(db, message.from_user.id)
    await message.answer(
        f"✅ Товар «{esc(product['name'])}» добавлен.",
        reply_markup=keyboards.main_keyboard(user.get_menu_buttons()),
    )


# ================= РЕДАКТИРОВАНИЕ / ДЕАКТИВАЦИЯ ТОВАРА =================

# Кнопка "✏️ Изменить цену/остаток" — показываем категории с action="edit".
@router.callback_query(F.data == "admin:edit_product")
async def admin_edit_product_start(callback: CallbackQuery, db) -> None:
    categories = await models.get_categories(db)
    await callback.message.answer(
        "Выберите категорию товара для редактирования:",
        reply_markup=keyboards.admin_categories_keyboard(categories, action="edit"),
    )
    await callback.answer()


# Кнопка "🚫 Деактивировать товар" — те же категории, но action="deactivate".
@router.callback_query(F.data == "admin:deactivate_product")
async def admin_deactivate_product_start(callback: CallbackQuery, db) -> None:
    categories = await models.get_categories(db)
    await callback.message.answer(
        "Выберите категорию товара для деактивации:",
        reply_markup=keyboards.admin_categories_keyboard(categories, action="deactivate"),
    )
    await callback.answer()


# Выбрана категория (для edit или deactivate) — показываем её товары.
# Этот обработчик стоит НИЖЕ admin_add_product_category, поэтому "admin_cat:add_product:..."
# сюда попадает, только если админ уже вышел из состояния waiting_category
# (например, нажал старую кнопку). Тогда просим начать заново.
@router.callback_query(F.data.startswith("admin_cat:"))
async def admin_category_chosen(callback: CallbackQuery, db) -> None:
    _, action, category_id_raw = callback.data.split(":")
    if action == "add_product":
        await callback.answer(
            "Добавление товара прервано. Начните заново: Админ-панель → ➕ Добавить товар",
            show_alert=True,
        )
        return

    # only_active=False — админ видит и деактивированные товары.
    products = await models.get_products(db, int(category_id_raw), only_active=False)
    if not products:
        await callback.answer("В этой категории нет товаров", show_alert=True)
        return
    # action передаётся дальше в кнопки товаров: "admin_product:edit:12" или "admin_product:deactivate:12".
    await callback.message.answer(
        "Выберите товар:", reply_markup=keyboards.admin_products_keyboard(products, action=action)
    )
    await callback.answer()


# Выбран товар для редактирования — спрашиваем, что менять (цену или остаток).
@router.callback_query(F.data.startswith("admin_product:edit:"))
async def admin_product_chosen_for_edit(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[2])
    await callback.message.answer(
        "Что изменить?", reply_markup=keyboards.admin_edit_field_keyboard(product_id)
    )
    await callback.answer()


# Выбран товар для деактивации — сразу деактивируем.
@router.callback_query(F.data.startswith("admin_product:deactivate:"))
async def admin_product_chosen_for_deactivation(callback: CallbackQuery, db) -> None:
    product_id = int(callback.data.split(":")[2])
    await models.deactivate_product(db, product_id)
    await callback.message.answer("🚫 Товар деактивирован и больше не виден в каталоге.")
    await callback.answer()


# Выбрано поле ("price" или "stock"). Запоминаем товар и поле, ждём новое значение.
@router.callback_query(F.data.startswith("admin_field:"))
async def admin_field_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    _, field, product_id_raw = callback.data.split(":")
    await state.update_data(product_id=int(product_id_raw), field=field)
    await state.set_state(EditProductStates.waiting_value)
    # Тернарный оператор: текст вопроса зависит от поля.
    prompt = "Введите новую цену:" if field == "price" else "Введите новый остаток на складе:"
    await callback.message.answer(prompt)
    await callback.answer()


# Пришло новое значение — проверяем и сохраняем.
@router.message(EditProductStates.waiting_value)
async def admin_edit_value(message: Message, state: FSMContext, db) -> None:
    data = await state.get_data()
    field = data["field"]
    product_id = data["product_id"]
    raw = (message.text or "").strip()

    if field == "price":
        price = parse_price(raw)
        if price is None:
            await message.answer(f"Введите цену числом от 0 до {MAX_PRICE}, например 199.90:")
            return
        await models.set_price(db, product_id, price)
        await message.answer(f"✅ Новая цена сохранена: {price} SUM")
    else:
        if not raw.isdecimal() or int(raw) > MAX_STOCK:
            await message.answer(f"Введите остаток целым числом от 0 до {MAX_STOCK}:")
            return
        await models.set_stock(db, product_id, int(raw))
        await message.answer(f"✅ Новый остаток сохранён: {raw} шт.")

    await state.clear()


# ================= ЗАКАЗЫ =================

# Статус в базе -> текст для админа.
STATUS_LABELS = {
    "pending": "Ожидает подтверждения",
    "confirmed": "Подтверждён",
    "ready": "Готов к выдаче",
    "completed": "Завершён",
    "cancelled": "Отменён",
}

# Шаблоны уведомлений ПОКУПАТЕЛЮ при смене статуса.
# {order_id} заполняется позже методом .format(order_id=...).
# Для "pending" шаблона нет — о нём покупателю не пишем.
CUSTOMER_STATUS_MESSAGES = {
    "confirmed": "📦 Заказ #{order_id} подтверждён и собирается.",
    "ready": "🎉 Заказ #{order_id} готов к выдаче/доставке!",
    "completed": "✅ Заказ #{order_id} завершён. Спасибо за покупку!",
    "cancelled": "❌ Заказ #{order_id} отменён. Если это ошибка — свяжитесь с аптекой.",
}


# Кнопка "📦 Все заказы" — последние 10 заказов, у каждого кнопки смены статуса.
@router.callback_query(F.data == "admin:orders")
async def admin_show_orders(callback: CallbackQuery, db) -> None:
    orders = await models.get_all_orders(db)
    if not orders:
        await callback.message.answer("Заказов пока нет.")
        await callback.answer()
        return

    for order in orders[:10]:
        text = (
            f"<b>Заказ #{order['order_id']}</b> от {order['created_at']:%d.%m.%Y %H:%M}\n"
            f"Статус: {STATUS_LABELS.get(order['status'], order['status'])}\n"
            f"Сумма: {order['total_amount']} SUM\n"
            f"Адрес: {esc(order['delivery_address'])}"
        )
        await callback.message.answer(text, reply_markup=keyboards.order_status_keyboard(order["order_id"]))
    await callback.answer()


# Нажата кнопка смены статуса. callback_data: "order_status:<номер заказа>:<новый статус>".
@router.callback_query(F.data.startswith("order_status:"))
async def admin_change_order_status(callback: CallbackQuery, db) -> None:
    _, order_id_raw, new_status = callback.data.split(":")
    order_id = int(order_id_raw)

    order = await models.get_order(db, order_id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    # "Завершён" и "Отменён" — конечные статусы, менять их больше нельзя.
    # Убираем кнопки (reply_markup=None), чтобы их больше не нажимали.
    if order["status"] in ("completed", "cancelled"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(
            f"Заказ уже в конечном статусе: {STATUS_LABELS[order['status']]}", show_alert=True
        )
        return
    if order["status"] == new_status:
        await callback.answer(f"Заказ уже в статусе: {STATUS_LABELS.get(new_status, new_status)}")
        return

    # Меняем статус в базе. Дальше автоматически срабатывают SQL-триггеры:
    # запись в журнал аудита и (при отмене) возврат товара на склад.
    await models.set_order_status(db, order_id, new_status)

    # Пытаемся уведомить покупателя.
    notified = False
    template = CUSTOMER_STATUS_MESSAGES.get(new_status)
    customer_tg_id = await models.get_order_customer_tg_id(db, order_id)
    if template and customer_tg_id:
        try:
            # callback.bot.send_message — написать покупателю в его личный чат с ботом.
            await callback.bot.send_message(customer_tg_id, template.format(order_id=order_id))
            notified = True
        except Exception:
            pass   # покупатель мог заблокировать бота — не страшно

    label = STATUS_LABELS.get(new_status, new_status)

    # Для конечных статусов дописываем результат в текст сообщения и убираем кнопки.
    if new_status in ("completed", "cancelled"):
        emoji = "✅" if new_status == "completed" else "❌"
        try:
            # callback.message.html_text — текст сообщения вместе с HTML-разметкой (<b> и т.д.).
            await callback.message.edit_text(
                f"{callback.message.html_text}\n\n{emoji} <b>{label}</b>",
                reply_markup=None,
            )
        except Exception:
            # Если текст отредактировать не удалось — хотя бы убираем кнопки.
            await callback.message.edit_reply_markup(reply_markup=None)

    suffix = ", покупатель уведомлён" if notified else ""
    await callback.answer(f"Статус заказа обновлён: {label}{suffix}")


# ================= СТАТИСТИКА =================

# Кнопка "📊 Статистика".
@router.callback_query(F.data == "admin:stats")
async def admin_show_stats(callback: CallbackQuery, db) -> None:
    now = datetime.now(timezone.utc)
    # Выручка с (сейчас - 30 дней) до сейчас — SQL-функция pharmacy.get_revenue.
    revenue = await models.get_revenue(db, now - timedelta(days=30), now)
    # 5 самых продаваемых товаров — SQL-функция pharmacy.get_top_products.
    top_products = await models.get_top_products(db, 5)

    lines = [f"<b>Выручка за 30 дней:</b> {revenue} SUM", "", "<b>Топ-5 товаров по продажам:</b>"]
    if top_products:
        for row in top_products:
            lines.append(f"  • {row['product_name']} — {row['total_sold']} шт.")
    else:
        lines.append("  пока нет данных")

    await callback.message.answer("\n".join(lines))
    await callback.answer()
