"""Регистрация подключённых чатов и актуализация прав бота."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

from database import Database
from services.moderation import member_status

log = logging.getLogger(__name__)


def evaluate_bot_member(member) -> tuple[bool, bool]:
    """По данным о самом боте в чате возвращает (бот в чате, есть право блокировать участников)."""
    status = member_status(member)
    active = status in ("member", "administrator", "restricted")
    has_rights = status == "administrator" and bool(getattr(member, "can_restrict_members", False))
    return active, has_rights


async def refresh_chats(bot, db: Database) -> None:
    """При запуске проверяет все известные чаты: бот мог потерять права или быть удалён, пока не работал."""
    for chat in await db.list_chats(active_only=True):
        try:
            member = await bot.get_chat_member(chat["chat_id"], bot.id)
        except TelegramForbiddenError:
            await db.set_chat_state(chat["chat_id"], False, False)
            continue
        except TelegramBadRequest as exc:
            if "chat not found" in str(exc).lower():
                await db.set_chat_state(chat["chat_id"], False, False)
            else:
                log.warning("Не удалось проверить чат %s: %s", chat["chat_id"], exc)
            continue
        except TelegramAPIError as exc:
            log.warning("Не удалось проверить чат %s: %s", chat["chat_id"], exc)
            continue
        active, has_rights = evaluate_bot_member(member)
        await db.set_chat_state(chat["chat_id"], active, has_rights)
