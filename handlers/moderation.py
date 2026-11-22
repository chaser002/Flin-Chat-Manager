"""Команды управления участниками: kick, ban, unban и их глобальные версии."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Config
from database import Database
from middlewares.access import AdminAccessMiddleware
from services.confirmations import ConfirmationStore, KeyedLocks
from services.moderation import apply_to_chat, run_global
from utils import texts
from utils.helpers import display_name
from utils.targets import ParsedTarget, TargetError, parse_target, validate_target

log = logging.getLogger(__name__)

router = Router(name="moderation")
router.message.middleware(AdminAccessMiddleware())
router.callback_query.middleware(AdminAccessMiddleware())


async def _prepare_target(message: Message, command: CommandObject, bot: Bot, config: Config) -> ParsedTarget | None:
    """Определяет и проверяет пользователя. При ошибке отвечает в чат и возвращает None."""
    try:
        target = parse_target(message, command.args)
        validate_target(target.user_id, bot.id, message.from_user.id, config.admin_ids)
    except TargetError as exc:
        await message.reply(str(exc))
        return None
    return target


# ───────── локальные команды ─────────
async def _run_local(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, locks: KeyedLocks, action: str
) -> None:
    target = await _prepare_target(message, command, bot, config)
    if target is None:
        return
    chat, executor = message.chat, message.from_user
    # Блокировка не даёт двум одновременным командам для одного пользователя выполниться параллельно
    async with locks.hold(("local", chat.id, target.user_id)):
        result = await apply_to_chat(bot, chat.id, chat.title or str(chat.id), target.user_id, action)
        await db.log_action(action, executor.id, target.user_id, chat.id, target.reason, result.outcome.value)
    await message.reply(
        texts.local_reply(action, result.outcome, result.detail, target.user_id, display_name(executor), target.reason)
    )


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, locks: KeyedLocks):
    await _run_local(message, command, bot, db, config, locks, "kick")


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, locks: KeyedLocks):
    await _run_local(message, command, bot, db, config, locks, "ban")


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, locks: KeyedLocks):
    await _run_local(message, command, bot, db, config, locks, "unban")


# ───────── глобальные команды ─────────
async def _ask_global(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config,
    confirmations: ConfirmationStore, kind: str,
) -> None:
    target = await _prepare_target(message, command, bot, config)
    if target is None:
        return
    chats = await db.list_chats(active_only=True)
    if not chats:
        await message.reply("Нет подключённых активных чатов. Добавьте бота в группы и выдайте ему права администратора.")
        return
    executor = message.from_user
    pending = confirmations.create(
        kind, target.user_id, target.reason, executor.id, display_name(executor), message.chat.id, config.confirm_ttl
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Подтвердить", callback_data=f"g:y:{pending.token}"),
            InlineKeyboardButton(text="Отмена", callback_data=f"g:n:{pending.token}"),
        ]]
    )
    await message.reply(
        texts.confirm_text(kind, target.user_id, target.reason, len(chats), config.confirm_ttl), reply_markup=markup
    )


@router.message(Command("globalkick"))
async def cmd_globalkick(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, confirmations: ConfirmationStore
):
    await _ask_global(message, command, bot, db, config, confirmations, "globalkick")


@router.message(Command("globalban"))
async def cmd_globalban(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, confirmations: ConfirmationStore
):
    await _ask_global(message, command, bot, db, config, confirmations, "globalban")


@router.message(Command("globalunban"))
async def cmd_globalunban(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, confirmations: ConfirmationStore
):
    await _ask_global(message, command, bot, db, config, confirmations, "globalunban")


async def _edit(message, text: str) -> None:
    try:
        await message.edit_text(text)
    except TelegramAPIError as exc:
        log.warning("Не удалось изменить сообщение: %s", exc)


@router.callback_query(F.data.startswith("g:"))
async def on_global_confirmation(
    call: CallbackQuery, bot: Bot, db: Database, config: Config,
    confirmations: ConfirmationStore, global_lock: asyncio.Lock,
) -> None:
    parts = (call.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("y", "n"):
        await call.answer()
        return
    _, decision, token = parts
    pending = confirmations.get(token)
    if pending is None:
        await call.answer("Запрос устарел или уже выполнен.", show_alert=True)
        if call.message is not None:
            await _edit(call.message, "Запрос на глобальную операцию устарел или уже выполнен.")
        return
    if call.from_user.id != pending.executor_id:
        await call.answer("Подтвердить может только администратор, вызвавший команду.", show_alert=True)
        return
    if call.message is None or call.message.chat.id != pending.chat_id:
        await call.answer()
        return

    if decision == "n":
        confirmations.pop(token)
        await call.answer("Отменено")
        await _edit(call.message, "Глобальная операция отменена.")
        return

    if global_lock.locked():
        await call.answer("Другая глобальная операция ещё выполняется. Повторите позже.", show_alert=True)
        return
    if confirmations.pop(token) is None:  # одноразовое использование подтверждения
        await call.answer("Запрос устарел или уже выполнен.", show_alert=True)
        return

    await call.answer("Выполняю…")
    async with global_lock:
        await _edit(call.message, "Выполняю глобальную операцию. Это может занять некоторое время…")
        try:
            report = await run_global(
                bot, db, pending.kind, pending.target_id, pending.executor_id, pending.reason, pending.chat_id
            )
        except Exception:
            log.exception("Ошибка при выполнении глобальной операции")
            await _edit(call.message, "Глобальная операция прервана из-за непредвиденной ошибки. Подробности в журнале бота.")
            return
    secure = pending.chat_id in config.admin_chat_ids
    await _edit(call.message, texts.format_global_report(report, secure))
