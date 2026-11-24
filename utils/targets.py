"""Определение пользователя, к которому применяется команда: Reply, Telegram ID или @username."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from utils.helpers import display_name

ANONYMOUS_ADMIN_ID = 1087968824  # служебный аккаунт «Анонимный администратор»
MAX_REASON_LENGTH = 200
MAX_USER_ID = 10**13
USERNAME_RE = re.compile(r"^@([A-Za-z][A-Za-z0-9_]{4,31})$")


class TargetError(Exception):
    """Некорректная цель команды. Текст исключения безопасно показывать в чате."""


@dataclass(slots=True)
class ParsedTarget:
    user_id: int
    user: Any | None  # объект пользователя, если он известен из сообщения
    reason: str
    username: str | None = None  # если цель указана через @username

    @property
    def label(self) -> str:
        return f"@{self.username} (ID {self.user_id})" if self.username else f"ID {self.user_id}"


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
    """Разбирает цель команды: Reply на сообщение или Telegram ID.

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
        raise TargetError("Укажите пользователя: ответьте на его сообщение, укажите @username или Telegram ID.")
    if not (first.isascii() and first.isdigit()) or not 0 < int(first) < MAX_USER_ID:
        raise TargetError("Некорректный Telegram ID. Укажите @username, положительное число или ответьте на сообщение.")
    return ParsedTarget(int(first), None, clean_reason(rest))


def _text_mention(message):
    """Пользователь, выбранный из подсказки Telegram (так отмечают людей без @username)."""
    for entity in getattr(message, "entities", None) or []:
        kind = str(getattr(entity.type, "value", entity.type))
        if kind == "text_mention" and getattr(entity, "user", None) is not None:
            data = (message.text or "").encode("utf-16-le")  # смещения в Telegram считаются в UTF-16
            after = data[(entity.offset + entity.length) * 2:].decode("utf-16-le")
            return entity.user, clean_reason(after)
    return None


async def resolve_target(message, args: str | None, db, *, allow_empty: bool = False) -> ParsedTarget | None:
    """Определяет цель команды: Reply, отметка пользователя, @username или Telegram ID.

    Telegram не позволяет боту найти человека по @username, поэтому бот ищет его среди тех, кого уже видел
    в подключённых чатах (сообщения, вступления). Если человека бот не знает, нужен Reply или Telegram ID.
    """
    args = (args or "").strip()
    target: ParsedTarget | None
    if real_reply(message) is not None:
        target = parse_target(message, args, allow_empty=allow_empty)
    elif (mention := _text_mention(message)) is not None:
        user, reason = mention
        target = ParsedTarget(user.id, user, reason)
    else:
        first, _, rest = args.partition(" ")
        if first.startswith("@"):
            match = USERNAME_RE.match(first)
            if not match:
                raise TargetError("Некорректный @username.")
            rows = await db.find_users_by_username(match.group(1))
            if not rows:
                raise TargetError(
                    f"Пользователь {first} не найден: бот ещё не видел его в подключённых чатах. "
                    "Используйте Reply или Telegram ID."
                )
            if len(rows) > 1:
                raise TargetError("Найдено несколько пользователей с таким @username. Используйте Reply или Telegram ID.")
            target = ParsedTarget(rows[0]["user_id"], None, clean_reason(rest), username=match.group(1))
        else:
            target = parse_target(message, args, allow_empty=allow_empty)

    if target is not None and target.user is not None:
        await db.upsert_user(target.user.id, getattr(target.user, "username", None), display_name(target.user))
    return target


def validate_target(user_id: int, bot_id: int, executor_id: int, admin_ids) -> None:
    """Запрещает применять команды управления к боту, к себе и к авторизованным администраторам."""
    if user_id == bot_id:
        raise TargetError("Команду нельзя применить к самому боту.")
    if user_id == executor_id:
        raise TargetError("Нельзя применить команду к самому себе.")
    if user_id in admin_ids:
        raise TargetError("Нельзя применить команду к авторизованному администратору бота.")
