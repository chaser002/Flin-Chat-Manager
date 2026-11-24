"""Подключённые чаты: автоматическая регистрация и команда /chats."""
from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from config import Config
from database import Database
from middlewares.access import AdminAccessMiddleware
from services.chats import evaluate_bot_member
from utils import texts
from utils.helpers import esc, fmt_time

log = logging.getLogger(__name__)

# Регистрация чатов: события приходят от Telegram, проверка администратора не нужна
events_router = Router(name="chat_events")

router = Router(name="chats")
router.message.middleware(AdminAccessMiddleware())


@events_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot, db: Database, config: Config):
    """Бот добавлен в группу, удалён из неё или изменились его права."""
    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return
    active, has_rights = evaluate_bot_member(event.new_chat_member)
    title = chat.title or str(chat.id)
    actor = event.from_user.id if event.from_user else 0
    before = await db.get_chat(chat.id)

    if active and config.leave_unauthorized_chats and actor not in config.admin_ids:
        log.warning("Бот добавлен в чат %s неавторизованным пользователем %s и покидает его", chat.id, actor)
        try:
            await bot.leave_chat(chat.id)
        except TelegramAPIError as exc:
            log.warning("Не удалось покинуть чат %s: %s", chat.id, exc)
        await db.upsert_chat(chat.id, title, False, False, actor)
        await db.log_action("chat_disconnected", actor, bot.id, chat.id, "Чат добавлен неавторизованным пользователем", "success")
        return

    await db.upsert_chat(chat.id, title, active, has_rights, actor if active else None)
    if not active:
        kind = "chat_disconnected"
    elif before is None or not before["is_active"]:
        kind = "chat_connected"
    else:
        kind = "chat_updated"
    await db.log_action(kind, actor, bot.id, chat.id, None, "success")
    log.info("Чат «%s» (%s): %s, права блокировки: %s", title, chat.id, kind, has_rights)


@router.message(Command("chats"))
async def cmd_chats(message: Message, db: Database, config: Config):
    total, active, with_rights = await db.chat_counts()
    if message.chat.id not in config.admin_chat_ids:
        await message.reply(
            f"{texts.SECURE_ONLY}\n"
            f"ID этого чата: {message.chat.id}. Чтобы видеть подробности здесь, добавьте его в ADMIN_CHAT_IDS."
        )
        return
    chats = await db.list_chats()
    if not chats:
        await message.reply("Подключённых чатов пока нет. Добавьте бота в группу.")
        return
    lines = [f"Подключённые чаты: {total} (активных: {active}, с правом блокировки: {with_rights})", ""]
    for chat in chats:
        if not chat["is_active"]:
            state = "⛔ не активен"
        elif chat["has_rights"]:
            state = "✅ активен, права есть"
        else:
            state = "⚠️ активен, нет права блокировки"
        lines.append(
            f"{esc(chat['title'])} ({chat['chat_id']}) — {state}; подключён {fmt_time(chat['connected_at'], config.tz_offset)}"
        )
    await message.reply("\n".join(lines)[:4000])
