"""Тексты ответов бота."""
from __future__ import annotations

from services.moderation import GlobalReport
from utils.errors import FAILED, OUTCOME_LABELS, Outcome
from utils.helpers import chats_word, esc, fmt_time, sentence

NO_REASON = "Не указана"

GLOBAL_TITLES = {
    "globalkick": "Глобальное исключение завершено.",
    "globalban": "Глобальная блокировка завершена.",
    "globalunban": "Глобальное снятие блокировки завершено.",
}
GLOBAL_DESCRIPTIONS = {
    "globalkick": "исключение из всех подключённых чатов",
    "globalban": "блокировка во всех подключённых чатах",
    "globalunban": "снятие блокировки во всех подключённых чатах",
}
RESULT_LABELS = {
    **{o.value: OUTCOME_LABELS[o] for o in Outcome},
    "success": "успешно",
    "partial": "частично",
    "failed": "не удалось",
    "skipped": "пропущено",
}
ACTION_LABELS = {
    "kick": "kick",
    "ban": "ban",
    "unban": "unban",
    "globalkick": "globalkick",
    "globalban": "globalban",
    "globalunban": "globalunban",
    "addblacklist": "addblacklist",
    "removeblacklist": "removeblacklist",
    "autoban": "autoban",
    "autonotify": "autonotify",
    "chat_connected": "чат подключён",
    "chat_disconnected": "чат отключён",
    "chat_updated": "права бота изменены",
}

SECURE_ONLY = "Эта информация доступна только в служебных чатах администрации."


def reason_line(reason: str) -> str:
    return f"Причина: {sentence(esc(reason) if reason else NO_REASON)}"


def local_reply(action: str, outcome: Outcome, detail: str, user_id: int, admin_name: str, reason: str) -> str:
    uid = f"ID пользователя: {user_id}"
    if outcome is Outcome.SUCCESS:
        head = {
            "kick": "Пользователь успешно исключён из текущего чата.",
            "ban": "Пользователь успешно заблокирован в текущем чате.",
            "unban": "Блокировка пользователя в текущем чате успешно снята.",
        }[action]
        lines = [head, uid, f"Администратор: {esc(admin_name)}"]
        if action != "unban":
            lines.append(reason_line(reason))
        return "\n".join(lines)
    if outcome is Outcome.ABSENT:
        return f"Пользователь отсутствует в текущем чате.\n{uid}"
    if outcome is Outcome.ALREADY:
        text = "Пользователь уже заблокирован в текущем чате." if action == "ban" else "Пользователь не заблокирован в текущем чате."
        return f"{text}\n{uid}"
    if outcome is Outcome.NO_RIGHTS:
        return f"У бота недостаточно прав в этом чате. Выдайте боту право блокировки пользователей.\n{uid}"
    if outcome is Outcome.PROTECTED:
        return f"Нельзя применить команду к администратору или владельцу чата.\n{uid}"
    if outcome is Outcome.UNAVAILABLE:
        return f"Не удалось выполнить команду: чат недоступен для бота.\n{uid}"
    return f"Не удалось выполнить команду. Ошибка Telegram: {esc(detail)}\n{uid}"


def confirm_text(kind: str, user_id: int, reason: str, chats_count: int, ttl: int) -> str:
    return (
        "Подтвердите глобальную операцию.\n\n"
        f"Действие: {GLOBAL_DESCRIPTIONS[kind]}\n"
        f"ID пользователя: {user_id}\n"
        f"{reason_line(reason)}\n"
        f"Подключённых чатов: {chats_count}\n\n"
        f"Запрос действителен {ttl} с. Подтвердить может только администратор, вызвавший команду."
    )


def format_global_report(report: GlobalReport, secure: bool) -> str:
    lines = [
        GLOBAL_TITLES[report.kind],
        "",
        f"Успешно: {chats_word(report.success)}.",
        f"Не удалось: {chats_word(report.failed)}.",
    ]
    if report.skipped:
        lines.append(f"Пропущено: {chats_word(report.skipped)} (пользователя нет в чате или нужное состояние уже достигнуто).")
    lines += [f"Всего проверено: {chats_word(report.total)}.", "", "Подробности доступны в отчёте."]

    breakdown = report.breakdown()
    if breakdown:
        reasons = ", ".join(f"{OUTCOME_LABELS[o]} — {n}" for o, n in breakdown.items())
        lines += ["", f"Причины неудач: {reasons}."]
    if secure:
        details = [r for r in report.results if r.outcome is not Outcome.SUCCESS]
        if details:
            lines += ["", "Подробный отчёт по чатам:"]
            for r in details[:25]:
                extra = f" ({esc(r.detail)})" if r.detail and r.outcome in FAILED else ""
                lines.append(f"• {esc(r.title)} — {OUTCOME_LABELS[r.outcome]}{extra}")
            if len(details) > 25:
                lines.append(f"…и ещё {len(details) - 25}")
    return "\n".join(lines)[:4000]


def blacklist_added(user_id: int, reason: str, admin_name: str) -> str:
    return f"Пользователь добавлен в Blacklist.\n\nID: {user_id}\n{reason_line(reason)}\nДобавил: {sentence(esc(admin_name))}"


def format_log_row(row: dict, tz_offset: int) -> str:
    action = ACTION_LABELS.get(row["action_type"], row["action_type"])
    result = RESULT_LABELS.get(row["result"], row["result"])
    reason = f" · причина: {esc(row['reason'])}" if row["reason"] else ""
    return (
        f"{fmt_time(row['created_at'], tz_offset)} · {esc(action)} · исполнитель {row['executor_id']} → "
        f"пользователь {row['target_id']} · чат {row['chat_id']} · {result}{reason}"
    )
