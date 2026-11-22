"""Подтверждение глобальных операций и защита от одновременного повторного выполнения."""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass


@dataclass(slots=True)
class PendingGlobal:
    token: str
    kind: str
    target_id: int
    reason: str
    executor_id: int
    executor_name: str
    chat_id: int
    expires_at: float


class ConfirmationStore:
    """Хранит запросы на подтверждение. Каждый запрос одноразовый и действует ограниченное время."""

    def __init__(self, clock=time.monotonic) -> None:
        self._items: dict[str, PendingGlobal] = {}
        self._clock = clock

    def create(
        self, kind: str, target_id: int, reason: str, executor_id: int, executor_name: str, chat_id: int, ttl: int
    ) -> PendingGlobal:
        self._purge()
        pending = PendingGlobal(
            token=secrets.token_urlsafe(8),
            kind=kind,
            target_id=target_id,
            reason=reason,
            executor_id=executor_id,
            executor_name=executor_name,
            chat_id=chat_id,
            expires_at=self._clock() + ttl,
        )
        self._items[pending.token] = pending
        return pending

    def get(self, token: str) -> PendingGlobal | None:
        self._purge()
        return self._items.get(token)

    def pop(self, token: str) -> PendingGlobal | None:
        self._purge()
        return self._items.pop(token, None)

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, p in self._items.items() if p.expires_at <= now]:
            del self._items[token]


class KeyedLocks:
    """Блокировки по ключу: две одновременные команды для одного пользователя в одном чате выполняются по очереди."""

    def __init__(self) -> None:
        self._locks: dict = {}

    @contextlib.asynccontextmanager
    async def hold(self, key):
        if len(self._locks) > 5000:
            self._locks = {k: v for k, v in self._locks.items() if v.locked()}
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield
