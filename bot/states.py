"""
states.py — СОСТОЯНИЯ ДЛЯ ПОШАГОВЫХ ДИАЛОГОВ (FSM).

FSM (Finite State Machine, конечный автомат) — способ запомнить,
на каком шаге диалога сейчас находится пользователь.

Пример — регистрация:
    бот: "Введите имя"      -> состояние waiting_first_name
    бот: "Введите фамилию"  -> состояние waiting_last_name
    ... и так далее.
Когда приходит сообщение, aiogram смотрит на текущее состояние и вызывает
нужный обработчик. Так бот понимает, что "Иван" — это имя, а не адрес.

Где используется:
    handlers/registration.py -> RegistrationStates
    handlers/cart.py         -> CheckoutStates
    handlers/admin.py        -> AddCategoryStates, AddProductStates, EditProductStates
"""

# State       — одно состояние (один шаг диалога).
# StatesGroup — группа связанных состояний (один диалог целиком).
from aiogram.fsm.state import State, StatesGroup

# Регистрация нового пользователя: 5 шагов по порядку.
class RegistrationStates(StatesGroup):
    waiting_first_name = State()   # ждём имя
    waiting_last_name = State()    # ждём фамилию
    waiting_phone = State()        # ждём телефон
    waiting_age = State()          # ждём возраст
    waiting_address = State()      # ждём адрес

# Оформление заказа: один шаг — ввод адреса доставки.
class CheckoutStates(StatesGroup):
    waiting_address = State()

# Админ добавляет категорию: название, потом описание.
class AddCategoryStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()

# Админ добавляет товар: 7 шагов.
class AddProductStates(StatesGroup):
    waiting_category = State()             # выбор категории (кнопкой)
    waiting_name = State()                 # название
    waiting_manufacturer = State()         # производитель
    waiting_description = State()          # описание
    waiting_price = State()                # цена
    waiting_stock = State()                # количество на складе
    waiting_prescription_flag = State()    # нужен ли рецепт (да/нет)

# Админ меняет цену или остаток товара: ждём новое значение.
class EditProductStates(StatesGroup):
    waiting_value = State()
