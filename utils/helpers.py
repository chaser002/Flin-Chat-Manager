"""Вспомогательные функции форматирования."""
from __future__ import annotations

import html
import time


def esc(value) -> str:
    """Экранирует текст для HTML-разметки Telegram."""
    return html.escape(str(value), quote=False)


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Склонение по числу: (1 чат, 2 чата, 5 чатов)."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def chats_word(n: int) -> str:
    return f"{n} {plural(n, ('чат', 'чата', 'чатов'))}"


def fmt_time(ts: int, tz_offset: int = 0) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(ts + tz_offset * 3600))


def sentence(text: str) -> str:
    """Добавляет точку в конце, если там нет знака препинания."""
    text = text.rstrip()
    return text if not text or text[-1] in ".!?…" else text + "."


def display_name(user) -> str:
    """Имя пользователя для сообщений бота."""
    name = getattr(user, "full_name", None)
    if name:
        return name
    username = getattr(user, "username", None)
    return f"@{username}" if username else str(getattr(user, "id", "—"))


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'
