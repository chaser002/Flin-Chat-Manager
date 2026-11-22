"""Получение имени пользователя из доступных источников."""
from __future__ import annotations

from aiogram.exceptions import TelegramAPIError

from database import Database
from utils.helpers import display_name


async def resolve_user_name(bot, db: Database, chat_id: int, user_id: int, user=None) -> str | None:
    """Имя берётся из сообщения, из сохранённых данных или из текущего чата. Если не найдено, возвращает None."""
    if user is not None:
        return display_name(user)
    known = await db.get_known_user(user_id)
    if known:
        return known["full_name"]
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return None
    return display_name(member.user)
