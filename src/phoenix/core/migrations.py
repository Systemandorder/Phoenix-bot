"""Версійні міграції БД (PRAGMA user_version).

Правила: ТІЛЬКИ ДОДАВАННЯ (створити/додати стовпець/індекс), ніколи не видаляти й не переписувати дані.
v1 — базова схема, сумісна зі старим ботом: безпечно запускати на живому botsort.db.
Перед будь-якою міграцією автоматично робиться резервна копія.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from .db import Database

log = logging.getLogger(__name__)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    # table/column/definition — константи з коду, ніколи не ввід користувача.
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _m001_baseline(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, group_name TEXT,
        is_vip INTEGER DEFAULT 0, has_paid INTEGER DEFAULT 0, free_clicks INTEGER DEFAULT 5)""")
    for col, definition in (("username", "TEXT"), ("energy_group", "TEXT"),
                            ("ui_theme", "TEXT DEFAULT 'DEFAULT'"), ("subscription_end", "TIMESTAMP")):
        _add_column(conn, "users", col, definition)

    conn.execute("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)")

    conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, target TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")

    conn.execute("""CREATE TABLE IF NOT EXISTS file_permissions (
        file_uid TEXT PRIMARY KEY, file_path TEXT, group_name TEXT, can_view INTEGER DEFAULT 1)""")
    # якщо таблиця вже існувала зі старою схемою — доганяємо відсутні стовпці
    for col, definition in (("file_path", "TEXT"), ("group_name", "TEXT"), ("can_view", "INTEGER DEFAULT 1")):
        _add_column(conn, "file_permissions", col, definition)

    conn.execute("CREATE TABLE IF NOT EXISTS server_paths (uid TEXT PRIMARY KEY, full_path TEXT)")
    _add_column(conn, "server_paths", "click_count", "INTEGER DEFAULT 0")

    conn.execute("""CREATE TABLE IF NOT EXISTS user_clicks (
        user_id INTEGER, file_uid TEXT, clicks INTEGER DEFAULT 1, PRIMARY KEY (user_id, file_uid))""")


def _m002_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON audit_logs (user_id, timestamp)")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline_legacy_compatible", _m001_baseline),
    (2, "audit_indexes", _m002_indexes),
]


def migrate(db: Database, backup_dir: Path | None = None) -> int:
    """Застосувати всі відсутні міграції. Повертає поточну версію схеми."""
    current = db.query_one("PRAGMA user_version")[0]
    latest = MIGRATIONS[-1][0]
    if current > latest:
        # БД створена новішою версією коду: не чіпаємо, щоб нічого не зламати
        raise RuntimeError(
            f"Версія БД ({current}) новіша за версію коду ({latest}). Онови код бота."
        )
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return current

    has_data = db.query_one("SELECT count(*) FROM sqlite_master")[0] > 0
    if backup_dir and has_data:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"botsort-v{current}-{stamp}.db"
        db.backup_to(dest)
        log.info("Резервна копія БД перед міграцією: %s", dest)

    for version, name, fn in pending:
        with db.tx() as conn:
            # перевірка ВСЕРЕДИНІ транзакції: якщо інший процес уже встиг — пропускаємо
            if conn.execute("PRAGMA user_version").fetchone()[0] >= version:
                continue
            fn(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
        log.info("Застосовано міграцію v%s (%s)", version, name)
    return pending[-1][0]
