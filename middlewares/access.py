"""Проверка прав: команды управления доступны только авторизованным администраторам."""
from __future__ import annotations

import logging
import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

log = logging.getLogger(__name__)


class AdminAccessMiddleware(BaseMiddleware):
    """Выполняется перед каждой командой управления и перед каждым подтверждением."""

    def __init__(self, notice_cooldown: int = 60) -> None:
        self._notice_cooldown = notice_cooldown
        self._last_notice: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        config = data["config"]
        user = getattr(event, "from_user", None)
        if user is not None and not user.is_bot and user.id in config.admin_ids:
            return await handler(event, data)

        user_id = getattr(user, "id", 0)
        log.warning("Отказ в доступе: пользователь %s попытался выполнить команду управления", user_id)
        if isinstance(event, CallbackQuery):
            await event.answer("Недостаточно прав для выполнения действия.", show_alert=True)
        elif isinstance(event, Message):
            now = time.monotonic()
            if now - self._last_notice.get(user_id, -1e9) >= self._notice_cooldown:
                self._last_notice[user_id] = now
                await event.reply("Недостаточно прав для выполнения команды.")
        return None
