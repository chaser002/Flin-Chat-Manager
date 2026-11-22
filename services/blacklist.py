"""Реакция на вступление пользователя из Blacklist в подключённый чат."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError

from config import Config
from database import Database
from services.moderation import apply_to_chat
from utils.errors import OUTCOME_LABELS, Outcome
from utils.helpers import esc, mention

log = logging.getLogger(__name__)


async def handle_blacklisted_join(bot, db: Database, config: Config, chat, user) -> str | None:
    """Если вступивший есть в Blacklist, выполняет заданное действие. Возвращает тип выполненного действия."""
    entry = await db.get_blacklist_entry(user.id)
    if entry is None or user.id == bot.id:
        return None

    outcome_note = ""
    if config.blacklist_action == "ban":
        res = await apply_to_chat(bot, chat.id, chat.title or str(chat.id), user.id, "ban")
        await db.log_action("autoban", bot.id, user.id, chat.id, "Автоматически: пользователь в Blacklist", res.outcome.value)
        if res.outcome in (Outcome.SUCCESS, Outcome.ALREADY):
            await _send(bot, chat.id, f"Пользователь из Blacklist автоматически заблокирован в этом чате.\nID: {user.id}")
            return "autoban"
        outcome_note = f"\nАвтоматическая блокировка не выполнена: {OUTCOME_LABELS[res.outcome]}."
    else:
        await db.log_action("autonotify", bot.id, user.id, chat.id, "Пользователь из Blacklist вступил в чат", "success")

    admins = " ".join(mention(admin_id, "администратор") for admin_id in sorted(config.admin_ids)[:5])
    await _send(
        bot,
        chat.id,
        f"Внимание! В чат вступил пользователь из Blacklist.\nID: {user.id}{outcome_note}\n"
        f"Требуется внимание администрации. {admins}".strip(),
    )
    return "notify"


async def _send(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError as exc:
        log.warning("Не удалось отправить уведомление в чат %s: %s", chat_id, esc(exc))
