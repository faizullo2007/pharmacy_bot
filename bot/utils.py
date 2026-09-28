"""
utils.py — ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ, которые используются в разных обработчиках.

    esc()              — защищает текст от HTML-тегов
    safe_edit_text()   — редактирует текст сообщения, не падая на "ничего не изменилось"
    safe_edit_markup() — то же самое, но только для кнопок
"""

# escape — стандартная функция Python: заменяет < > & на &lt; &gt; &amp;
from html import escape
# TelegramBadRequest — ошибка, которую aiogram выбрасывает, если Telegram отклонил запрос.
from aiogram.exceptions import TelegramBadRequest

# Зачем нужна: бот работает в режиме HTML (см. main.py, ParseMode.HTML).
# Если пользователь введёт, например, имя "<Иван>", Telegram примет это за тег и выдаст ошибку.
# esc() превращает "<Иван>" в "&lt;Иван&gt;" — и оно отображается как обычный текст.
# str(value) — на случай, если передали не строку (число, None и т.п.).
# quote=False — кавычки не трогаем, они в тексте сообщения безопасны.
# "-> str" — подсказка типа: функция возвращает строку.
def esc(value) -> str:
    return escape(str(value), quote=False)

# Редактирует уже отправленное сообщение (текст + кнопки).
# Используется, когда пользователь листает каталог или меняет корзину —
# вместо нового сообщения обновляется старое.
async def safe_edit_text(message, text, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        # Если новый текст совпадает со старым, Telegram возвращает ошибку
        # "message is not modified". Это не проблема — просто игнорируем.
        # Любую другую ошибку пробрасываем дальше (raise).
        if "message is not modified" not in str(e):
            raise

# То же самое, но меняем только кнопки под сообщением (текст остаётся прежним).
async def safe_edit_markup(message, reply_markup) -> None:
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
