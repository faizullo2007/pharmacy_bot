"""
config.py — НАСТРОЙКИ БОТА.

Зачем нужен: все секретные и изменяемые значения (токен бота, пароль от базы,
список админов) хранятся НЕ в коде, а в файле .env. Этот модуль читает .env
и превращает его содержимое в обычные переменные Python.

Кто использует: main.py — берёт отсюда BOT_TOKEN, ADMIN_IDS и DATABASE_DSN.
"""

# os — стандартный модуль Python для работы с операционной системой.
# Нам нужна функция os.getenv(), которая читает "переменные окружения".
import os

# load_dotenv — функция из библиотеки python-dotenv (см. requirements.txt).
# Она открывает файл .env и загружает строки вида КЛЮЧ=значение
# в переменные окружения, чтобы потом их можно было прочитать через os.getenv().
from dotenv import load_dotenv

# Вызываем загрузку .env. После этой строки os.getenv("BOT_TOKEN") вернёт значение из файла.
load_dotenv()

# Токен бота — "пароль", который выдаёт @BotFather в Telegram.
# По нему Telegram понимает, что это именно наш бот.
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ADMIN_IDS в .env записан строкой, например "123,456".
# Второй аргумент "" — значение по умолчанию, если переменной нет (чтобы не было None).
# Имя начинается с "_" — это соглашение: "внутренняя переменная, снаружи не использовать".
_admin_ids_raw = os.getenv("ADMIN_IDS", "")

# List comprehension (генератор списка) — короткая запись цикла:
#   .split(",")      — режем строку по запятым: "123,456" -> ["123", "456"]
#   if value.strip() — пропускаем пустые куски (strip() убирает пробелы; пустая строка = False)
#   int(value)       — превращаем текст "123" в число 123
# Итог: ADMIN_IDS = [123, 456] — список Telegram-ID администраторов.
ADMIN_IDS = [int(value) for value in _admin_ids_raw.split(",") if value.strip()]

# Параметры подключения к PostgreSQL.
# Если в .env чего-то нет — берётся значение по умолчанию (второй аргумент getenv).
DB_HOST = os.getenv("DB_HOST", "localhost")          # адрес сервера БД (localhost = этот же компьютер)
DB_PORT = os.getenv("DB_PORT", "5432")               # 5432 — стандартный порт PostgreSQL
DB_NAME = os.getenv("DB_NAME", "pharmacy_bot")       # имя базы (создаётся в sql/00_bootstrap.sql)
DB_USER = os.getenv("DB_USER", "pharmacy_bot_app")   # пользователь БД (создаётся в sql/04_roles_grants.sql)
DB_PASSWORD = os.getenv("DB_PASSWORD")               # пароль — без значения по умолчанию, он обязателен

# DSN (Data Source Name) — одна строка со всеми данными для подключения к БД.
# f"..." — f-строка: вместо {DB_HOST} и т.д. подставляются значения переменных.
# Две f-строки подряд в скобках Python склеивает в одну.
# Эту строку main.py передаёт в класс Database (db.py).
DATABASE_DSN = (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
    f"user={DB_USER} password={DB_PASSWORD}"
)

# Проверки при запуске: без токена или пароля бот работать не сможет,
# поэтому сразу "падаем" с понятной ошибкой, а не где-то потом с непонятной.
# raise — выбросить исключение (ошибку) и остановить программу.
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан — проверьте файл .env")
if not DB_PASSWORD:
    raise RuntimeError("DB_PASSWORD не задан — проверьте файл .env")
