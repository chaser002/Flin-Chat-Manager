"""Единый чёрный список: команды администраторов и реакция на вступление пользователей."""
from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, Message

from config import Config
from database import Database
from middlewares.access import AdminAccessMiddleware
from services.blacklist import handle_blacklisted_join
from services.users import resolve_user_name
from utils import texts
from utils.helpers import display_name, esc, fmt_time
from utils.targets import TargetError, parse_target, validate_target

log = logging.getLogger(__name__)

# Команды управления: только для авторизованных администраторов
router = Router(name="blacklist")
router.message.middleware(AdminAccessMiddleware())

# События вступления: обрабатываются для всех пользователей, без проверки администратора
events_router = Router(name="blacklist_events")


async def _target(message: Message, command: CommandObject, bot: Bot, config: Config):
    try:
        target = parse_target(message, command.args)
        validate_target(target.user_id, bot.id, message.from_user.id, config.admin_ids)
    except TargetError as exc:
        await message.reply(str(exc))
        return None
    return target


@router.message(Command("addblacklist"))
async def cmd_add(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config):
    target = await _target(message, command, bot, config)
    if target is None:
        return
    executor = message.from_user
    name = await resolve_user_name(bot, db, message.chat.id, target.user_id, target.user) or "Неизвестно"
    reason = target.reason or texts.NO_REASON
    if not await db.add_blacklist(target.user_id, name, reason, executor.id):
        await message.reply(f"Пользователь уже находится в Blacklist.\n\nID: {target.user_id}")
        return
    await db.log_action("addblacklist", executor.id, target.user_id, message.chat.id, reason, "success")
    await message.reply(texts.blacklist_added(target.user_id, reason, display_name(executor)))


@router.message(Command("removeblacklist"))
async def cmd_remove(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config):
    target = await _target(message, command, bot, config)
    if target is None:
        return
    executor = message.from_user
    if not await db.remove_blacklist(target.user_id):
        await message.reply(f"Пользователь не найден в Blacklist.\n\nID: {target.user_id}")
        return
    await db.log_action("removeblacklist", executor.id, target.user_id, message.chat.id, target.reason, "success")
    await message.reply(
        f"Пользователь удалён из Blacklist.\n\nID: {target.user_id}\nУдалил: {esc(display_name(executor))}."
    )


@router.message(Command("blacklist"))
async def cmd_list(message: Message, db: Database, config: Config):
    if message.chat.id not in config.admin_chat_ids:
        await message.reply(f"{texts.SECURE_ONLY}\nЗаписей в Blacklist: {await db.count_blacklist()}.")
        return
    total = await db.count_blacklist()
    if not total:
        await message.reply("Blacklist пуст.")
        return
    lines = [f"Blacklist ({total}):"]
    for row in await db.list_blacklist(20):
        lines.append(
            f"• {row['user_id']} · {esc(row['name'])} · {fmt_time(row['created_at'], config.tz_offset)} · "
            f"добавил {row['added_by']}\n   Причина: {esc(row['reason'])}"
        )
    if total > 20:
        lines.append(f"Показаны последние 20 из {total}.")
    await message.reply("\n".join(lines)[:4000])


@router.message(Command("checkblacklist"))
async def cmd_check(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config):
    try:
        target = parse_target(message, command.args)
    except TargetError as exc:
        await message.reply(str(exc))
        return
    if message.chat.id not in config.admin_chat_ids:
        await message.reply(f"Результат проверки не публикуется в открытом чате. {texts.SECURE_ONLY}")
        return
    entry = await db.get_blacklist_entry(target.user_id)
    if entry is None:
        await message.reply(f"Пользователь не найден в Blacklist.\n\nID: {target.user_id}")
        return
    await message.reply(
        "Пользователь находится в Blacklist.\n\n"
        f"ID: {entry['user_id']}\nИмя: {esc(entry['name'])}\nПричина: {esc(entry['reason'])}\n"
        f"Дата добавления: {fmt_time(entry['created_at'], config.tz_offset)}\nДобавил (ID): {entry['added_by']}"
    )


# ───────── вступление пользователей ─────────
_recent_joins: dict[tuple[int, int], float] = {}


async def _on_join(bot: Bot, db: Database, config: Config, chat, user) -> None:
    """Одно вступление может прийти и как chat_member, и как служебное сообщение: повтор отбрасывается."""
    key = (chat.id, user.id)
    now = time.monotonic()
    if now - _recent_joins.get(key, -1e9) < 30:
        return
    if len(_recent_joins) > 5000:
        _recent_joins.clear()
    if await db.get_blacklist_entry(user.id) is None:
        return
    _recent_joins[key] = now
    await handle_blacklisted_join(bot, db, config, chat, user)


@events_router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, bot: Bot, db: Database, config: Config):
    if event.chat.type not in ("group", "supergroup"):
        return
    old, new = str(getattr(event.old_chat_member.status, "value", event.old_chat_member.status)), str(
        getattr(event.new_chat_member.status, "value", event.new_chat_member.status)
    )
    if old in ("left", "kicked") and new in ("member", "restricted"):
        await _on_join(bot, db, config, event.chat, event.new_chat_member.user)


@events_router.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot, db: Database, config: Config):
    for user in message.new_chat_members or []:
        await _on_join(bot, db, config, message.chat, user)
