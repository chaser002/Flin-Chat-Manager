"""Определение пользователя, к которому применяется команда: Reply или Telegram ID."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ANONYMOUS_ADMIN_ID = 1087968824  # служебный аккаунт «Анонимный администратор»
MAX_REASON_LENGTH = 200
MAX_USER_ID = 10**13


class TargetError(Exception):
    """Некорректная цель команды. Текст исключения безопасно показывать в чате."""


@dataclass(slots=True)
class ParsedTarget:
    user_id: int
    user: Any | None  # объект пользователя, если команда вызвана ответом на сообщение
    reason: str


def real_reply(message):
    """Ответ на настоящее сообщение. В темах Telegram каждое сообщение «отвечает» на заголовок темы."""
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return None
    if getattr(reply, "forum_topic_created", None) is not None:
        return None
    if getattr(message, "is_topic_message", False) and reply.message_id == getattr(message, "message_thread_id", None):
        return None
    return reply


def clean_reason(text: str) -> str:
    return " ".join(text.split())[:MAX_REASON_LENGTH]


def parse_target(message, args: str | None, *, allow_empty: bool = False) -> ParsedTarget | None:
    """Разбирает цель команды.

    Reply на сообщение: причина — весь текст после команды.
    Telegram ID: первый аргумент — ID, остальное — причина.
    """
    args = (args or "").strip()
    reply = real_reply(message)
    if reply is not None:
        user = getattr(reply, "from_user", None)
        if getattr(reply, "sender_chat", None) is not None or user is None or user.id == ANONYMOUS_ADMIN_ID:
            raise TargetError("Команду нельзя применить к каналу или анонимному администратору.")
        return ParsedTarget(user.id, user, clean_reason(args))

    first, _, rest = args.partition(" ")
    if not first:
        if allow_empty:
            return None
        raise TargetError("Укажите пользователя: ответьте на его сообщение или укажите Telegram ID.")
    if not (first.isascii() and first.isdigit()) or not 0 < int(first) < MAX_USER_ID:
        raise TargetError("Некорректный Telegram ID. Укажите положительное число или ответьте на сообщение пользователя.")
    return ParsedTarget(int(first), None, clean_reason(rest))


def validate_target(user_id: int, bot_id: int, executor_id: int, admin_ids) -> None:
    """Запрещает применять команды управления к боту, к себе и к авторизованным администраторам."""
    if user_id == bot_id:
        raise TargetError("Команду нельзя применить к самому боту.")
    if user_id == executor_id:
        raise TargetError("Нельзя применить команду к самому себе.")
    if user_id in admin_ids:
        raise TargetError("Нельзя применить команду к авторизованному администратору бота.")
