"""Фильтрация входящих событий: бот работает только в группах и не реагирует на ботов."""
from __future__ import annotations

from collections import OrderedDict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

GROUP_TYPES = ("group", "supergroup")


def event_chat(event):
    if isinstance(event, CallbackQuery):
        return getattr(event.message, "chat", None)
    return getattr(event, "chat", None)


class GroupOnlyMiddleware(BaseMiddleware):
    """Пропускает только события из групп и супергрупп.

    Личные сообщения игнорируются полностью: бот не отвечает в личке и не пишет пользователям первым.
    События от ботов (в том числе от самого бота) и от анонимных администраторов отбрасываются.
    """

    async def __call__(self, handler, event, data):
        chat = event_chat(event)
        if chat is None or chat.type not in GROUP_TYPES:
            return None
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return None
        return await handler(event, data)


class DedupMiddleware(BaseMiddleware):
    """Не даёт обработать одно и то же сообщение дважды."""

    def __init__(self, capacity: int = 2000) -> None:
        self._seen: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._capacity = capacity

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            key = (event.chat.id, event.message_id)
            if key in self._seen:
                return None
            self._seen[key] = None
            if len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return await handler(event, data)
