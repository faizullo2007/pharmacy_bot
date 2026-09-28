"""
main.py — ТОЧКА ВХОДА. С этого файла бот запускается.

Порядок работы:
    1. Подключаемся к базе данных (db.py).
    2. Делаем админами всех, кто указан в ADMIN_IDS (config.py -> models.py).
    3. Создаём объект бота (Bot) и диспетчер (Dispatcher).
    4. Подключаем "роутеры" — наборы обработчиков из папки handlers/.
    5. Запускаем polling — бесконечный опрос Telegram: "есть новые сообщения?"

Схема связей всего проекта:
    config.py  -> настройки из .env
    db.py      -> соединение с PostgreSQL
    models.py  -> SQL-запросы и классы User / Admin / Order
    states.py  -> состояния пошаговых диалогов (FSM)
    keyboards.py -> кнопки
    utils.py   -> вспомогательные функции
    handlers/  -> реакции бота на сообщения и нажатия кнопок
"""

# asyncio — стандартная библиотека Python для асинхронного кода (async/await).
import asyncio
# aiogram — библиотека для Telegram-ботов.
#   Bot        — объект, через который бот ОТПРАВЛЯЕТ запросы в Telegram (сообщения и т.д.)
#   Dispatcher — "распределитель": ПОЛУЧАЕТ обновления и решает, какой обработчик вызвать.
from aiogram import Bot, Dispatcher
# DefaultBotProperties — настройки "по умолчанию" для всех сообщений бота.
from aiogram.client.default import DefaultBotProperties
# ParseMode.HTML — Telegram будет понимать HTML-теги в тексте: <b>жирный</b> и т.п.
from aiogram.enums import ParseMode
# MemoryStorage — хранилище состояний FSM в оперативной памяти
# (например, "пользователь сейчас на шаге ввода телефона"). При перезапуске бота оно очищается.
from aiogram.fsm.storage.memory import MemoryStorage
# Наши собственные модули:
import config                                             # настройки (config.py)
from db import Database                                   # класс подключения к БД (db.py)
from models import ensure_tg_id_known, grant_admin        # функции из models.py
from handlers import admin, cart, catalog, common, orders, registration  # файлы из папки handlers/

# Главная асинхронная функция — вся логика запуска внутри неё.
async def main():
    # 1. Создаём объект базы и подключаемся.
    db = Database(config.DATABASE_DSN)
    await db.connect()
    # flush=True — вывести текст в консоль сразу, без буферизации.
    print("Подключились к базе данных", flush=True)

    # 2. Для каждого ID из .env (ADMIN_IDS):
    for tg_id in config.ADMIN_IDS:
        # сначала добавляем его в users.tg_users (таблица admin ссылается на неё внешним ключом),
        await ensure_tg_id_known(db, tg_id)
        # затем выдаём права администратора (SQL-функция users.grant_admin).
        await grant_admin(db, tg_id)

    # 3. Создаём бота. token — из .env; parse_mode=HTML — во всех сообщениях работают HTML-теги.
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # Диспетчер с хранилищем состояний в памяти.
    dp = Dispatcher(storage=MemoryStorage())
    # ВАЖНО: кладём объект db в диспетчер под ключом "db".
    # Благодаря этому в любом обработчике можно написать параметр `db`,
    # и aiogram сам подставит туда этот объект (это называется "внедрение зависимостей").
    dp["db"] = db

    # 4. Подключаем роутеры. ПОРЯДОК ВАЖЕН: aiogram проверяет их сверху вниз
    # и вызывает первый подходящий обработчик.
    dp.include_router(registration.router)  # /start, регистрация, профиль
    dp.include_router(admin.router)         # админ-панель (срабатывает только для админов)
    dp.include_router(catalog.router)       # каталог товаров
    dp.include_router(cart.router)          # корзина и оформление заказа
    dp.include_router(orders.router)        # "Мои заказы"
    dp.include_router(common.router)        # /help и "не понимаю" — ПОСЛЕДНИЙ, ловит всё остальное

    try:
        # Удаляем webhook (если был) и выбрасываем сообщения, пришедшие, пока бот был выключен.
        await bot.delete_webhook(drop_pending_updates=True)
        print("Бот запущен, жду сообщений...", flush=True)
        # 5. Запускаем бесконечный цикл получения обновлений от Telegram.
        await dp.start_polling(bot)
    finally:
        # Когда бот остановлен (Ctrl+C или ошибка) — обязательно закрываем соединение с БД.
        await db.close()

# Эта проверка истинна, только если файл запущен напрямую (python main.py),
# а не импортирован из другого файла.
if __name__ == "__main__":
    # asyncio.run — запускает асинхронную функцию main().
    # loop_factory=asyncio.SelectorEventLoop — нужно для Windows:
    # асинхронный psycopg не работает со стандартным для Windows циклом событий (Proactor).
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
