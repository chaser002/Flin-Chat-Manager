"""Работа с базой данных SQLite.

Запросы выполняются в отдельном потоке и последовательно, поэтому цикл событий не блокируется.
Все таблицы создаются автоматически при первом запуске.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id      INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    has_rights   INTEGER NOT NULL DEFAULT 0,
    connected_at INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    added_by     INTEGER
);

CREATE TABLE IF NOT EXISTS known_users (
    user_id   INTEGER PRIMARY KEY,
    username  TEXT,
    full_name TEXT NOT NULL,
    last_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_known_username ON known_users (username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS blacklist (
    user_id    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    reason     TEXT    NOT NULL,
    added_by   INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT,
    is_summary   INTEGER NOT NULL DEFAULT 1,
    action_type  TEXT    NOT NULL,
    executor_id  INTEGER NOT NULL,
    target_id    INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    reason       TEXT,
    result       TEXT    NOT NULL,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_summary ON actions (is_summary, id);
CREATE INDEX IF NOT EXISTS idx_actions_target  ON actions (target_id, is_summary);
"""

# Типы действий, которые считаются наказаниями в истории пользователя
PUNISHMENT_TYPES = ("kick", "ban", "globalkick", "globalban", "autoban")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ───────── служебное ─────────
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        return conn

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open)
        await self._run(lambda: self._conn.executescript(SCHEMA))

    async def close(self) -> None:
        if self._conn is not None:
            await self._run(self._conn.close)
            self._conn = None

    async def _run(self, fn: Callable[[], Any]) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn)

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        return await self._run(lambda: [dict(r) for r in self._conn.execute(sql, params).fetchall()])

    async def _fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        def work() -> dict | None:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

        return await self._run(work)

    async def _write(self, sql: str, params: tuple = ()) -> int:
        def work() -> int:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

        return await self._run(work)

    # ───────── чаты ─────────
    async def upsert_chat(
        self, chat_id: int, title: str, is_active: bool, has_rights: bool, added_by: int | None = None
    ) -> None:
        now = int(time.time())
        await self._write(
            """
            INSERT INTO chats (chat_id, title, is_active, has_rights, connected_at, updated_at, added_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                connected_at = CASE WHEN chats.is_active = 0 AND excluded.is_active = 1
                                    THEN excluded.connected_at ELSE chats.connected_at END,
                added_by = CASE WHEN chats.is_active = 0 AND excluded.is_active = 1
                                THEN excluded.added_by ELSE chats.added_by END,
                is_active = excluded.is_active,
                has_rights = excluded.has_rights,
                updated_at = excluded.updated_at
            """,
            (chat_id, title, int(is_active), int(has_rights), now, now, added_by),
        )

    async def set_chat_state(self, chat_id: int, is_active: bool, has_rights: bool) -> None:
        await self._write(
            "UPDATE chats SET is_active=?, has_rights=?, updated_at=? WHERE chat_id=?",
            (int(is_active), int(has_rights), int(time.time()), chat_id),
        )

    async def get_chat(self, chat_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM chats WHERE chat_id=?", (chat_id,))

    async def list_chats(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM chats"
        if active_only:
            sql += " WHERE is_active=1"
        return await self._fetchall(sql + " ORDER BY title COLLATE NOCASE, chat_id")

    async def chat_counts(self) -> tuple[int, int, int]:
        """Возвращает (всего, активных, активных с правами)."""
        row = await self._fetchone(
            "SELECT COUNT(*) AS total, COALESCE(SUM(is_active), 0) AS active, "
            "COALESCE(SUM(CASE WHEN is_active=1 AND has_rights=1 THEN 1 ELSE 0 END), 0) AS rights FROM chats"
        )
        return row["total"], row["active"], row["rights"]

    # ───────── известные пользователи ─────────
    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        await self._write(
            "INSERT INTO known_users (user_id, username, full_name, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, "
            "last_seen=excluded.last_seen",
            (user_id, username, full_name, int(time.time())),
        )

    async def get_known_user(self, user_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM known_users WHERE user_id=?", (user_id,))

    async def find_users_by_username(self, username: str) -> list[dict]:
        """Ищет среди пользователей, которых бот видел в чатах. Регистр не важен."""
        return await self._fetchall("SELECT * FROM known_users WHERE username = ? COLLATE NOCASE", (username,))

    # ───────── blacklist ─────────
    async def add_blacklist(self, user_id: int, name: str, reason: str, added_by: int) -> bool:
        """Добавляет пользователя. Возвращает False, если он уже в списке."""
        changed = await self._write(
            "INSERT OR IGNORE INTO blacklist (user_id, name, reason, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, reason, added_by, int(time.time())),
        )
        return changed == 1

    async def remove_blacklist(self, user_id: int) -> bool:
        return await self._write("DELETE FROM blacklist WHERE user_id=?", (user_id,)) == 1

    async def get_blacklist_entry(self, user_id: int) -> dict | None:
        return await self._fetchone("SELECT * FROM blacklist WHERE user_id=?", (user_id,))

    async def list_blacklist(self, limit: int = 20) -> list[dict]:
        return await self._fetchall("SELECT * FROM blacklist ORDER BY created_at DESC, user_id DESC LIMIT ?", (limit,))

    async def count_blacklist(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS n FROM blacklist")
        return row["n"]

    # ───────── журнал действий ─────────
    async def log_action(
        self,
        action_type: str,
        executor_id: int,
        target_id: int,
        chat_id: int,
        reason: str | None,
        result: str,
        operation_id: str | None = None,
        is_summary: bool = True,
    ) -> None:
        await self._write(
            "INSERT INTO actions (operation_id, is_summary, action_type, executor_id, target_id, chat_id, reason, "
            "result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (operation_id, int(is_summary), action_type, executor_id, target_id, chat_id, reason or None, result,
             int(time.time())),
        )

    async def recent_actions(self, limit: int = 10) -> list[dict]:
        """Последние действия. Детальные записи по чатам глобальных операций не включаются."""
        return await self._fetchall("SELECT * FROM actions WHERE is_summary=1 ORDER BY id DESC LIMIT ?", (limit,))

    async def punishment_history(self, user_id: int, limit: int = 5) -> list[dict]:
        marks = ",".join("?" * len(PUNISHMENT_TYPES))
        return await self._fetchall(
            f"SELECT * FROM actions WHERE target_id=? AND is_summary=1 AND action_type IN ({marks}) "
            "AND result IN ('success', 'partial') ORDER BY id DESC LIMIT ?",
            (user_id, *PUNISHMENT_TYPES, limit),
        )

    async def punishment_count(self, user_id: int) -> int:
        marks = ",".join("?" * len(PUNISHMENT_TYPES))
        row = await self._fetchone(
            f"SELECT COUNT(*) AS n FROM actions WHERE target_id=? AND is_summary=1 AND action_type IN ({marks}) "
            "AND result IN ('success', 'partial')",
            (user_id, *PUNISHMENT_TYPES),
        )
        return row["n"]
