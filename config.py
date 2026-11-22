"""Загрузка конфигурации из файла .env и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Ошибка конфигурации: бот не может быть запущен."""


def load_dotenv(path: str | Path = ".env") -> None:
    """Читает файл .env, если он есть. Уже заданные переменные окружения не перезаписываются."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key:
            os.environ.setdefault(key, value)


def parse_ids(value: str, name: str) -> frozenset[int]:
    """Разбирает список чисел через запятую."""
    result: set[int] = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"{name}: «{part}» не является числом") from exc
    return frozenset(result)


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: ожидается целое число") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name}: значение должно быть от {low} до {high}")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]  # авторизованные администраторы: только они управляют ботом
    admin_chat_ids: frozenset[int]  # служебные чаты, где разрешены подробные административные данные
    db_path: str
    blacklist_action: str  # "ban" или "notify"
    confirm_ttl: int  # сколько секунд действует подтверждение глобальной операции
    tz_offset: int  # часовой пояс для отображения дат
    leave_unauthorized_chats: bool  # выходить из чатов, в которые бота добавил не администратор
    log_level: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN не задан. Укажите токен бота от @BotFather в .env или в переменных окружения")

    admin_ids = parse_ids(os.getenv("ADMIN_IDS", ""), "ADMIN_IDS")
    if not admin_ids:
        raise ConfigError("ADMIN_IDS не задан. Укажите Telegram ID авторизованных администраторов через запятую")

    action = os.getenv("BLACKLIST_ACTION", "ban").strip().lower() or "ban"
    if action not in ("ban", "notify"):
        raise ConfigError("BLACKLIST_ACTION должен быть ban или notify")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        admin_chat_ids=parse_ids(os.getenv("ADMIN_CHAT_IDS", ""), "ADMIN_CHAT_IDS"),
        db_path=os.getenv("DB_PATH", "data/flin_chat_manager.db").strip() or "data/flin_chat_manager.db",
        blacklist_action=action,
        confirm_ttl=_int("CONFIRM_TTL_SECONDS", 90, 15, 600),
        tz_offset=_int("TZ_OFFSET_HOURS", 2, -12, 14),
        leave_unauthorized_chats=os.getenv("LEAVE_UNAUTHORIZED_CHATS", "0").strip() == "1",
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
