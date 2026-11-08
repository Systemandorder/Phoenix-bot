"""Журнал дій (хто і що зробив). Помилка запису ніколи не ламає роботу бота."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .db import Database

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, db: Database, timezone: str = "Europe/Kyiv") -> None:
        self._db = db
        self._tz = ZoneInfo(timezone)

    def log(self, user_id: int, action: str, target: str) -> None:
        """Записує дію. Формат часу такий самий, як у старих записах."""
        ts = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._db.execute(
                "INSERT INTO audit_logs (user_id, timestamp, action, target) VALUES (?, ?, ?, ?)",
                (user_id, ts, action, target),
            )
        except sqlite3.Error:
            log.exception("Не вдалося записати в журнал дій (користувач %s, дія %s)", user_id, action)

    async def alog(self, user_id: int, action: str, target: str) -> None:
        """Те саме, але не блокує бота."""
        await asyncio.to_thread(self.log, user_id, action, target)
