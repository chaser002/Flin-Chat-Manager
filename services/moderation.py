"""Бизнес-логика: исключение, блокировка и снятие блокировки в одном или во всех чатах.

Результат считается успешным только тогда, когда Telegram API подтвердил выполнение метода.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramAPIError

from database import Database
from utils.errors import FAILED, SKIPPED, Outcome, api_call, classify_api_error, is_basic_group_error

log = logging.getLogger(__name__)

GLOBAL_TO_LOCAL = {"globalkick": "kick", "globalban": "ban", "globalunban": "unban"}
PRESENT_STATUSES = {"member", "administrator", "creator", "restricted"}
SCAN_LIMIT = 60  # максимум чатов при проверке присутствия пользователя


def member_status(member) -> str:
    return str(getattr(member.status, "value", member.status))


@dataclass(slots=True)
class ChatResult:
    chat_id: int
    title: str
    outcome: Outcome
    detail: str = ""


async def apply_to_chat(bot, chat_id: int, title: str, user_id: int, action: str) -> ChatResult:
    """Применяет kick, ban или unban в одном чате и возвращает результат."""

    def result(outcome: Outcome, detail: str = "") -> ChatResult:
        return ChatResult(chat_id, title, outcome, detail)

    try:
        member = await api_call(bot.get_chat_member, chat_id, user_id)
        status = member_status(member)
    except TelegramAPIError as exc:
        outcome, detail = classify_api_error(exc)
        if outcome is not Outcome.ABSENT:
            return result(outcome, detail)
        status = "left"  # Telegram не знает такого участника в чате

    if status in ("creator", "administrator"):
        if action == "unban":
            return result(Outcome.ALREADY, "пользователь не заблокирован")
        return result(Outcome.PROTECTED, "администратор или владелец чата")

    if action == "kick":
        if status in ("left", "kicked"):
            return result(Outcome.ABSENT, "пользователя нет в чате")
        try:
            confirmed = await api_call(bot.ban_chat_member, chat_id, user_id)
        except TelegramAPIError as exc:
            return result(*classify_api_error(exc))
        if not confirmed:
            return result(Outcome.ERROR, "Telegram не подтвердил исключение")
        try:
            await api_call(bot.unban_chat_member, chat_id, user_id, only_if_banned=True)
        except TelegramAPIError as exc:
            if not is_basic_group_error(exc):
                return result(Outcome.ERROR, "пользователь исключён, но снять блокировку не удалось")
        return result(Outcome.SUCCESS)

    if action == "ban":
        if status == "kicked":
            return result(Outcome.ALREADY, "пользователь уже заблокирован")
        try:
            confirmed = await api_call(bot.ban_chat_member, chat_id, user_id)
        except TelegramAPIError as exc:
            return result(*classify_api_error(exc))
        if not confirmed:
            return result(Outcome.ERROR, "Telegram не подтвердил блокировку")
        return result(Outcome.SUCCESS, "заблокирован заранее, пользователя в чате не было" if status == "left" else "")

    if action == "unban":
        if status != "kicked":
            return result(Outcome.ALREADY, "пользователь не заблокирован")
        try:
            confirmed = await api_call(bot.unban_chat_member, chat_id, user_id, only_if_banned=True)
        except TelegramAPIError as exc:
            return result(*classify_api_error(exc))
        if not confirmed:
            return result(Outcome.ERROR, "Telegram не подтвердил снятие блокировки")
        return result(Outcome.SUCCESS)

    raise ValueError(f"Неизвестное действие: {action}")


@dataclass(slots=True)
class GlobalReport:
    kind: str
    operation_id: str
    results: list[ChatResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success(self) -> int:
        return sum(1 for r in self.results if r.outcome is Outcome.SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome in FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.outcome in SKIPPED)

    def breakdown(self) -> dict[Outcome, int]:
        """Количество неудач по причинам."""
        counts: dict[Outcome, int] = {}
        for outcome in (Outcome.NO_RIGHTS, Outcome.PROTECTED, Outcome.UNAVAILABLE, Outcome.ERROR):
            n = sum(1 for r in self.results if r.outcome is outcome)
            if n:
                counts[outcome] = n
        return counts

    @property
    def result_code(self) -> str:
        if self.success and not self.failed:
            return "success"
        if self.success:
            return "partial"
        if self.failed:
            return "failed"
        return "skipped"


async def run_global(
    bot,
    db: Database,
    kind: str,
    target_id: int,
    executor_id: int,
    reason: str,
    origin_chat_id: int,
    pause: float = 0.35,
) -> GlobalReport:
    """Выполняет globalkick, globalban или globalunban во всех активных подключённых чатах."""
    action = GLOBAL_TO_LOCAL[kind]
    chats = await db.list_chats(active_only=True)
    report = GlobalReport(kind=kind, operation_id=uuid.uuid4().hex[:12])
    for chat in chats:
        try:
            res = await apply_to_chat(bot, chat["chat_id"], chat["title"], target_id, action)
        except Exception:  # одна неудача не должна останавливать всю операцию
            log.exception("Непредвиденная ошибка в чате %s", chat["chat_id"])
            res = ChatResult(chat["chat_id"], chat["title"], Outcome.ERROR, "непредвиденная ошибка")
        if res.outcome is Outcome.UNAVAILABLE:
            await db.set_chat_state(chat["chat_id"], False, False)
        await db.log_action(
            kind, executor_id, target_id, res.chat_id, reason, res.outcome.value,
            operation_id=report.operation_id, is_summary=False,
        )
        report.results.append(res)
        if pause:
            await asyncio.sleep(pause)
    await db.log_action(
        kind, executor_id, target_id, origin_chat_id, reason, report.result_code,
        operation_id=report.operation_id, is_summary=True,
    )
    return report


async def scan_membership(bot, db: Database, user_id: int, limit: int = SCAN_LIMIT) -> tuple[int, int]:
    """Проверяет по API, в скольких подключённых чатах состоит пользователь. Возвращает (найден в, проверено)."""
    chats = (await db.list_chats(active_only=True))[:limit]
    present = 0
    for chat in chats:
        try:
            member = await api_call(bot.get_chat_member, chat["chat_id"], user_id)
        except TelegramAPIError:
            continue
        status = member_status(member)
        if status in ("creator", "administrator", "member") or (status == "restricted" and getattr(member, "is_member", False)):
            present += 1
    return present, len(chats)
