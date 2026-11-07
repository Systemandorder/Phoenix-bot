"""Versioned migrations (PRAGMA user_version).

Rules: ADDITIVE ONLY (create/add/index), never drop or rewrite data.
v1 is the legacy-compatible baseline: safe to run on the live botsort.db.
A backup is taken automatically before any pending migration.
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
    # table/column/definition are code constants, never user input.
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
    current = db.query_one("PRAGMA user_version")[0]
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return current

    has_data = db.query_one("SELECT count(*) FROM sqlite_master")[0] > 0
    if backup_dir and has_data:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"botsort-v{current}-{stamp}.db"
        db.backup_to(dest)
        log.info("DB backup before migration: %s", dest)

    for version, name, fn in pending:
        with db.tx() as conn:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
        log.info("Applied migration v%s (%s)", version, name)
    return pending[-1][0]
