"""Шар SQLite: одна точка входу, WAL, транзакції, async-обгортки.

Замінює 298 розкиданих `sqlite3.connect('botsort.db')` і глобальний
monkeypatch sqlite3.connect. Одне зʼєднання на потік (перевикористовується).
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
        self._local = threading.local()      # зʼєднання конкретного потоку
        self._lock = threading.Lock()        # захищає список _all і _gen
        self._all: list[sqlite3.Connection] = []  # усі відкриті зʼєднання (для close)
        self._gen = 0                        # номер "покоління": росте після close()

    def _conn(self) -> sqlite3.Connection:
        cached = getattr(self._local, "conn", None)
        if cached is not None and cached[0] == self._gen:
            return cached[1]
        # isolation_level=None -> autocommit; транзакції явні (tx()).
        # check_same_thread=False лише щоб close() міг закрити з головного потоку.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._all.append(conn)
            gen = self._gen
        self._local.conn = (gen, conn)
        return conn

    # ---- синхронний API ----
    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, params)

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Атомарний блок. Вкладений виклик приєднується до зовнішньої транзакції."""
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
        """Узгоджена онлайн-копія (безпечно, поки бот працює)."""
        target = sqlite3.connect(str(dest))
        try:
            self._conn().backup(target)
        finally:
            target.close()

    def close(self) -> None:
        """Закрити всі зʼєднання. Наступний запит відкриє свіже."""
        with self._lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    log.exception("Не вдалося закрити зʼєднання")
            self._all.clear()
            self._gen += 1  # старі зʼєднання в потоках стають недійсними

    # ---- async API (не блокує event loop) ----
    async def aexecute(self, sql: str, params: Params = ()) -> None:
        await asyncio.to_thread(self.execute, sql, params)

    async def aquery_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return await asyncio.to_thread(self.query_one, sql, params)

    async def aquery_all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self.query_all, sql, params)
