"""
handlers/registration.py — КОМАНДА /start, РЕГИСТРАЦИЯ и КНОПКА "👤 Профиль".

Как работает регистрация (пошаговый диалог через FSM, см. states.py):

    /start -> имя -> фамилия -> телефон -> возраст -> адрес -> сохранение в БД

На каждом шаге:
    1) проверяем введённое значение;
    2) сохраняем его во временное хранилище FSM: state.update_data(...);
    3) переключаем состояние на следующий шаг: state.set_state(...);
    4) задаём следующий вопрос.
В конце берём всё накопленное (state.get_data()) и записываем в таблицу users.users.
"""

from aiogram import F, Router
# CommandStart — фильтр для команды /start.
from aiogram.filters import CommandStart
# FSMContext — объект для работы с состоянием пользователя:
#   set_state()   — перейти на шаг
#   update_data() — запомнить значение
#   get_data()    — получить всё запомненное (словарь)
#   clear()       — сбросить состояние и данные
from aiogram.fsm.context import FSMContext
# ReplyKeyboardRemove — убрать reply-клавиатуру с экрана пользователя.
from aiogram.types import Message, ReplyKeyboardRemove

from bot import keyboards
from bot.models import get_current_user
from bot.states import RegistrationStates
from bot.utils import esc

router = Router(name="registration")


# /start — первая команда, с которой пользователь начинает общение с ботом.
# state: FSMContext и db — aiogram передаёт их автоматически.
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db) -> None:
    # Сбрасываем любой незаконченный диалог (если человек был на середине чего-то).
    await state.clear()
    tg_user = message.from_user   # данные Telegram-аккаунта: id, имя, username

    # Получаем User или Admin (см. models.get_current_user).
    user = await get_current_user(db, tg_user.id)
    # Сохраняем/обновляем данные Telegram в таблице users.tg_users.
    await user.save_tg_info(tg_user.first_name, tg_user.last_name, tg_user.username)

    # Уже зарегистрирован — просто показываем главное меню.
    if await user.is_registered():
        await message.answer(
            "С возвращением в аптеку! Выберите действие в меню.",
            # Для админа get_menu_buttons() вернёт ещё и "⚙️ Админ-панель" (полиморфизм).
            reply_markup=keyboards.main_keyboard(user.get_menu_buttons()),
        )
        return

    # Не зарегистрирован — начинаем регистрацию: переходим на шаг "ждём имя".
    await state.set_state(RegistrationStates.waiting_first_name)
    await message.answer(
        "Добро пожаловать в аптеку! Для оформления заказов нужно зарегистрироваться.\n\n"
        "Введите ваше имя:"
    )


# Шаг 1: срабатывает, только когда пользователь в состоянии waiting_first_name.
@router.message(RegistrationStates.waiting_first_name)
async def process_first_name(message: Message, state: FSMContext) -> None:
    # (message.text or "") — если текста нет (прислали стикер/фото), берём пустую строку,
    # чтобы .strip() не упал на None. .strip() убирает пробелы по краям.
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("Имя не может быть пустым. Введите ваше имя:")
        return   # состояние не меняем — пользователь остаётся на этом же шаге
    # 100 — такая же длина, как VARCHAR(100) в таблице users.users (sql/01_schema.sql).
    if len(first_name) > 100:
        await message.answer("Слишком длинное имя (максимум 100 символов). Введите ваше имя:")
        return
    await state.update_data(first_name=first_name)                  # запомнили имя
    await state.set_state(RegistrationStates.waiting_last_name)     # следующий шаг
    await message.answer("Введите вашу фамилию:")


# Шаг 2: фамилия.
@router.message(RegistrationStates.waiting_last_name)
async def process_last_name(message: Message, state: FSMContext) -> None:
    last_name = (message.text or "").strip()
    if not last_name:
        await message.answer("Фамилия не может быть пустой. Введите вашу фамилию:")
        return
    if len(last_name) > 100:
        await message.answer("Слишком длинная фамилия (максимум 100 символов). Введите вашу фамилию:")
        return
    await state.update_data(last_name=last_name)
    await state.set_state(RegistrationStates.waiting_phone)
    await message.answer(
        "Отправьте номер телефона кнопкой ниже или введите его вручную:",
        reply_markup=keyboards.phone_keyboard(),
    )


# Шаг 3а: телефон отправлен КНОПКОЙ (как контакт).
# Два фильтра сразу: нужное состояние И F.contact (в сообщении есть контакт).
# Этот обработчик стоит ВЫШЕ текстового варианта, поэтому контакт проверяется первым.
@router.message(RegistrationStates.waiting_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(RegistrationStates.waiting_age)
    # Убираем кнопку "Отправить номер", она больше не нужна.
    await message.answer("Сколько вам лет?", reply_markup=ReplyKeyboardRemove())


# Шаг 3б: телефон введён ТЕКСТОМ вручную.
@router.message(RegistrationStates.waiting_phone)
async def process_phone_text(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    # 20 — как VARCHAR(20) в таблице.
    if len(phone) < 5 or len(phone) > 20:
        await message.answer("Введите корректный номер телефона (от 5 до 20 символов):")
        return
    await state.update_data(phone=phone)
    await state.set_state(RegistrationStates.waiting_age)
    await message.answer("Сколько вам лет?")


# Шаг 4: возраст.
@router.message(RegistrationStates.waiting_age)
async def process_age(message: Message, state: FSMContext) -> None:
    raw_age = (message.text or "").strip()
    # .isdecimal() — True, если строка состоит только из цифр.
    # (0 <= x <= 120) — двойное сравнение; такое же ограничение CHECK стоит в таблице.
    if not raw_age.isdecimal() or not (0 <= int(raw_age) <= 120):
        await message.answer("Введите возраст числом от 0 до 120:")
        return
    await state.update_data(age=int(raw_age))
    await state.set_state(RegistrationStates.waiting_address)
    await message.answer("Введите ваш адрес (для доставки заказов):")


# Шаг 5 (последний): адрес и сохранение в базу.
@router.message(RegistrationStates.waiting_address)
async def process_address(message: Message, state: FSMContext, db) -> None:
    address = (message.text or "").strip()
    if not address:
        await message.answer("Адрес не может быть пустым. Введите ваш адрес:")
        return
    if len(address) > 255:
        await message.answer("Слишком длинный адрес (максимум 255 символов). Введите ваш адрес:")
        return

    tg_user = message.from_user
    # Достаём всё, что накопили на предыдущих шагах: {"first_name": ..., "last_name": ..., ...}
    data = await state.get_data()

    user = await get_current_user(db, tg_user.id)
    # Именованные аргументы (first_name=...) — понятно, какое значение куда идёт.
    await user.register(
        first_name=data["first_name"],
        last_name=data["last_name"],
        phone=data["phone"],
        age=data["age"],
        address=address,
    )
    # Регистрация закончена — выходим из состояния и очищаем временные данные.
    await state.clear()

    await message.answer(
        "Регистрация завершена! Теперь вам доступны покупки в аптеке.",
        reply_markup=keyboards.main_keyboard(user.get_menu_buttons()),
    )


# Кнопка меню "👤 Профиль" — показать данные пользователя.
@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message, db) -> None:
    tg_user = message.from_user
    user = await get_current_user(db, tg_user.id)
    profile = await user.get_profile()
    if profile is None:
        await message.answer("Профиль не найден. Отправьте /start для регистрации.")
        return

    # <b>...</b> — жирный шрифт (работает, т.к. в main.py включён ParseMode.HTML).
    # esc(...) — защита от HTML в данных, которые ввёл пользователь.
    await message.answer(
        "<b>Ваш профиль</b>\n"
        f"Имя: {esc(profile['first_name'])} {esc(profile['last_name'])}\n"
        f"Телефон: {esc(profile['phone'])}\n"
        f"Возраст: {profile['age']}\n"
        f"Адрес: {esc(profile['address'])}\n"
        f"Дата регистрации: {profile['registered_at']:%d.%m.%Y}"
    )
