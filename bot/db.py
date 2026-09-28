"""
db.py — ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ PostgreSQL.

Зачем нужен: здесь один класс Database — "обёртка" над библиотекой psycopg.
Остальной код не работает с psycopg напрямую, а вызывает простые методы:
    db.fetch_all(...)  — получить МНОГО строк (список)
    db.fetch_one(...)  — получить ОДНУ строку (или None)
    db.execute(...)    — выполнить запрос без результата (INSERT/UPDATE/DELETE)

Связи:
    main.py   — создаёт объект Database и кладёт его в dp["db"],
                после чего aiogram сам передаёт `db` в каждый обработчик.
    models.py — все SQL-запросы идут через методы этого класса.
"""

# psycopg (версия 3) — драйвер для работы с PostgreSQL из Python.
import psycopg
# dict_row — "фабрика строк": каждая строка результата будет словарём
# {"название_колонки": значение}, а не кортежем. Поэтому в коде пишем row["name"], а не row[1].
from psycopg.rows import dict_row


class Database:
    # __init__ — конструктор: вызывается при создании объекта Database(dsn).
    # self — ссылка на сам объект (сюда сохраняем его данные).
    def __init__(self, dsn):
        self.dsn = dsn      # строка подключения из config.DATABASE_DSN
        self.conn = None    # само соединение пока не открыто (None = "пусто")

    # async def — асинхронная функция (корутина). Её вызывают через await.
    # Пока ждём ответ от базы, бот может обрабатывать сообщения других людей.
    async def connect(self):
        # Открываем асинхронное соединение с PostgreSQL.
        self.conn = await psycopg.AsyncConnection.connect(
            self.dsn,
            # autocommit=True — каждый запрос сразу сохраняется в базе
            # (не нужно вручную писать commit()).
            autocommit=True,
            # Строки результата будут словарями (см. импорт dict_row выше).
            row_factory=dict_row,
        )

    async def close(self):
        # Закрываем соединение, только если оно было открыто.
        if self.conn is not None:
            await self.conn.close()

    # _run — "внутренний" метод (начинается с _), его вызывают только методы ниже.
    # query  — текст SQL с заполнителями %s
    # params — кортеж значений, которые psycopg безопасно подставит вместо %s
    #          (это защищает от SQL-инъекций — нельзя "впихнуть" свой SQL через ввод пользователя).
    async def _run(self, query, params):
        try:
            # cursor (курсор) — объект, через который выполняется запрос и читаются результаты.
            cur = self.conn.cursor()
            await cur.execute(query, params)
            return cur
        # Если соединение с базой оборвалось (перезапуск сервера, сеть и т.п.) —
        # переподключаемся и пробуем выполнить запрос ещё раз.
        except (psycopg.OperationalError, psycopg.InterfaceError):
            await self.connect()
            cur = self.conn.cursor()
            await cur.execute(query, params)
            return cur

    # params=None — параметр необязательный: у запроса может не быть %s.
    async def fetch_all(self, query, params=None):
        cur = await self._run(query, params)
        # try/finally — блок finally выполнится ВСЕГДА (даже при ошибке),
        # поэтому курсор гарантированно закроется.
        try:
            return await cur.fetchall()   # список словарей (все строки)
        finally:
            await cur.close()

    async def fetch_one(self, query, params=None):
        cur = await self._run(query, params)
        try:
            return await cur.fetchone()   # один словарь или None, если строк нет
        finally:
            await cur.close()

    async def execute(self, query, params=None):
        # Результат не нужен — просто выполняем и закрываем курсор.
        cur = await self._run(query, params)
        await cur.close()
