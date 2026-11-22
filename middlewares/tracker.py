"""Запоминание пользователей, которых бот видит в подключённых чатах."""
from __future__ import annotations

import logging
import time

from aiogram import BaseMiddleware

log = logging.getLogger(__name__)


class UserTrackerMiddleware(BaseMiddleware):
    """Сохраняет только Telegram ID, имя и username. Нужен, чтобы показывать имя, если команда вызвана по ID."""

    def __init__(self, refresh_seconds: int = 600) -> None:
        self._seen: dict[int, tuple[str | None, str, float]] = {}
        self._refresh = refresh_seconds

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        db = data.get("db")
        if user is not None and db is not None and not user.is_bot:
            now = time.monotonic()
            cached = self._seen.get(user.id)
            if cached is None or cached[:2] != (user.username, user.full_name) or now - cached[2] > self._refresh:
                try:
                    await db.upsert_user(user.id, user.username, user.full_name)
                except Exception:
                    log.exception("Не удалось сохранить данные пользователя")
                if len(self._seen) > 20000:
                    self._seen.clear()
                self._seen[user.id] = (user.username, user.full_name, now)
        return await handler(event, data)
