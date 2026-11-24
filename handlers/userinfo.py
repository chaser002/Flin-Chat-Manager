"""Информация о пользователе: открытая часть для всех и административная часть только в служебных чатах."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from database import Database
from services.moderation import scan_membership
from services.users import resolve_user_name
from utils import texts
from utils.helpers import esc, fmt_time
from utils.targets import TargetError, resolve_target

router = Router(name="userinfo")  # доступна всем участникам


@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config):
    requester = message.from_user
    try:
        target = await resolve_target(message, command.args, db, allow_empty=True)
    except TargetError as exc:
        await message.reply(str(exc))
        return
    if target is None:  # без аргументов — информация о самом авторе команды
        user_id, user = requester.id, requester
    else:
        user_id, user = target.user_id, target.user

    name = await resolve_user_name(bot, db, message.chat.id, user_id, user) or "неизвестно"
    lines = ["Информация о пользователе.", "", f"Имя: {esc(name)}", f"ID: {user_id}"]

    if requester.id in config.admin_ids:
        if message.chat.id not in config.admin_chat_ids:
            # Административные сведения не публикуются в открытых чатах
            lines += ["", "Административные сведения доступны только в служебных чатах."]
        else:
            lines += await _admin_details(bot, db, config, user_id)
    await message.reply("\n".join(lines))


async def _admin_details(bot: Bot, db: Database, config: Config, user_id: int) -> list[str]:
    lines = [""]
    entry = await db.get_blacklist_entry(user_id)
    if entry:
        lines += [
            "Blacklist: да",
            f"Дата добавления: {fmt_time(entry['created_at'], config.tz_offset)}",
            f"Причина: {esc(entry['reason'])}",
        ]
    else:
        lines.append("Blacklist: нет")
    present, checked = await scan_membership(bot, db, user_id)
    lines.append(f"Обнаружен в подключённых чатах: {present} из {checked}")
    history = await db.punishment_history(user_id, 5)
    count = await db.punishment_count(user_id)
    if history:
        lines.append(f"История наказаний (последние {len(history)} из {count}):")
        for row in history:
            reason = f" · {esc(row['reason'])}" if row["reason"] else ""
            lines.append(f"• {fmt_time(row['created_at'], config.tz_offset)} · {row['action_type']}{reason}")
    else:
        lines.append("История наказаний: пусто")
    return lines
