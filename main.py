"""Flin Chat Manager: централизованное управление участниками нескольких Telegram-групп."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import ConfigError, load_config, load_dotenv
from database import Database
from handlers import blacklist, chats, logs, moderation, userinfo
from middlewares.chat_filter import DedupMiddleware, GroupOnlyMiddleware
from middlewares.tracker import UserTrackerMiddleware
from services.chats import refresh_chats
from services.confirmations import ConfirmationStore, KeyedLocks

log = logging.getLogger("flin")


async def main() -> None:
    load_dotenv()
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"Ошибка конфигурации: {exc}") from exc

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(config.db_path)
    await db.connect()  # база и таблицы создаются при первом запуске

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.workflow_data.update(
        config=config,
        db=db,
        confirmations=ConfirmationStore(),
        locks=KeyedLocks(),
        global_lock=asyncio.Lock(),
    )

    # Общие фильтры: только группы, без ботов, без повторной обработки одного сообщения
    dp.message.outer_middleware(GroupOnlyMiddleware())
    dp.callback_query.outer_middleware(GroupOnlyMiddleware())
    dp.message.outer_middleware(DedupMiddleware())
    dp.message.outer_middleware(UserTrackerMiddleware())

    dp.include_router(chats.events_router)
    dp.include_router(blacklist.events_router)
    dp.include_router(moderation.router)
    dp.include_router(blacklist.router)
    dp.include_router(chats.router)
    dp.include_router(logs.router)
    dp.include_router(userinfo.router)

    try:
        me = await bot.get_me()
        log.info("Flin Chat Manager запущен как @%s (ID %s)", me.username, me.id)
        await refresh_chats(bot, db)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            raise
