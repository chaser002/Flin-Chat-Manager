"""Журнал административных действий."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from database import Database
from middlewares.access import AdminAccessMiddleware
from utils import texts

router = Router(name="logs")
router.message.middleware(AdminAccessMiddleware())


@router.message(Command("logs"))
async def cmd_logs(message: Message, command: CommandObject, db: Database, config: Config):
    if message.chat.id not in config.admin_chat_ids:
        await message.reply(f"Журнал не публикуется в открытом чате. {texts.SECURE_ONLY}")
        return
    arg = (command.args or "").strip()
    limit = int(arg) if arg.isascii() and arg.isdigit() else 10
    limit = max(1, min(limit, 20))
    rows = await db.recent_actions(limit)
    if not rows:
        await message.reply("Журнал пуст.")
        return
    lines = [f"Последние действия ({len(rows)}):", ""]
    lines += [texts.format_log_row(row, config.tz_offset) for row in rows]
    await message.reply("\n".join(lines)[:4000])
