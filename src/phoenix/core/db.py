"""SQLite layer: one entry point, WAL, transactions, async wrappers.

Replaces 298 ad-hoc `sqlite3.connect('botsort.db')` calls and the global
monkeypatch of sqlite3.connect. One connection per thread (reused).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger(__name__)
Params = Sequence[Any] | dict[str, Any]


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # isolation_level=None -> autocommit; transactions are explicit (tx()).
            # check_same_thread=False only so close() can run from the main thread.
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            with self._lock:
                self._all.append(conn)
        return conn

    # ---- sync API ----
    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, params)

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Atomic block. Nested use joins the outer transaction."""
        conn = self._conn()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def backup_to(self, dest: str | Path) -> None:
        """Consistent online backup (safe while the bot is running)."""
        target = sqlite3.connect(str(dest))
        try:
            self._conn().backup(target)
        finally:
            target.close()

    def close(self) -> None:
        with self._lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    log.exception("Failed to close connection")
            self._all.clear()
        self._local = threading.local()

    # ---- async API (does not block the event loop) ----
    async def aexecute(self, sql: str, params: Params = ()) -> None:
        await asyncio.to_thread(self.execute, sql, params)

    async def aquery_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return await asyncio.to_thread(self.query_one, sql, params)

    async def aquery_all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self.query_all, sql, params)
