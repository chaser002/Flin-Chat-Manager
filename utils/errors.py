"""Классификация ошибок Telegram API и безопасный вызов методов."""
from __future__ import annotations

import asyncio
import logging
from enum import StrEnum

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    """Результат действия в одном чате."""

    SUCCESS = "success"  # Telegram подтвердил выполнение
    ABSENT = "absent"  # пользователя нет в чате
    ALREADY = "already"  # нужное состояние уже достигнуто
    NO_RIGHTS = "no_rights"  # у бота нет нужных прав
    PROTECTED = "protected"  # цель — администратор или владелец чата
    UNAVAILABLE = "unavailable"  # чат недоступен боту
    ERROR = "error"  # прочая ошибка


FAILED = frozenset({Outcome.NO_RIGHTS, Outcome.PROTECTED, Outcome.UNAVAILABLE, Outcome.ERROR})
SKIPPED = frozenset({Outcome.ABSENT, Outcome.ALREADY})

OUTCOME_LABELS = {
    Outcome.SUCCESS: "выполнено",
    Outcome.ABSENT: "пользователя нет в чате",
    Outcome.ALREADY: "нужное состояние уже достигнуто",
    Outcome.NO_RIGHTS: "у бота нет прав",
    Outcome.PROTECTED: "администратор или владелец чата",
    Outcome.UNAVAILABLE: "чат недоступен боту",
    Outcome.ERROR: "ошибка Telegram",
}


def classify_api_error(exc: Exception) -> tuple[Outcome, str]:
    """Переводит исключение Telegram API в результат и краткое пояснение."""
    text = str(exc).lower()
    if isinstance(exc, TelegramRetryAfter):
        return Outcome.ERROR, "превышен лимит запросов Telegram"
    if isinstance(exc, TelegramForbiddenError):
        return Outcome.UNAVAILABLE, "бот удалён из чата или заблокирован"
    if isinstance(exc, TelegramBadRequest):
        if "chat not found" in text:
            return Outcome.UNAVAILABLE, "чат не найден"
        if any(s in text for s in ("administrator of the chat", "can't remove chat owner", "chat owner")):
            return Outcome.PROTECTED, "цель — администратор или владелец чата"
        if any(s in text for s in ("not enough rights", "need to be administrator", "chat_admin_required", "no rights")):
            return Outcome.NO_RIGHTS, "у бота недостаточно прав"
        if any(s in text for s in ("user not found", "participant_id_invalid", "user_id_invalid", "member not found")):
            return Outcome.ABSENT, "пользователь не найден в чате"
    return Outcome.ERROR, (str(exc) or exc.__class__.__name__)[:120]


def is_basic_group_error(exc: Exception) -> bool:
    """Снятие блокировки недоступно в обычных группах: там исключённый пользователь может вернуться сам."""
    text = str(exc).lower()
    return isinstance(exc, TelegramBadRequest) and "supergroup" in text and "channel" in text


async def api_call(method, *args, **kwargs):
    """Вызывает метод Telegram API и при ограничении частоты запросов ждёт и повторяет попытку."""
    for attempt in range(3):
        try:
            return await method(*args, **kwargs)
        except TelegramRetryAfter as exc:
            if attempt == 2 or exc.retry_after > 30:
                raise
            log.warning("Лимит запросов Telegram, пауза %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
