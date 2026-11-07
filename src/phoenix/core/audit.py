"""Audit trail (who did what). Never raises into handlers."""
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
        ts = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S")  # same format as legacy rows
        try:
            self._db.execute(
                "INSERT INTO audit_logs (user_id, timestamp, action, target) VALUES (?, ?, ?, ?)",
                (user_id, ts, action, target),
            )
        except sqlite3.Error:
            log.exception("Audit write failed (user=%s action=%s)", user_id, action)

    async def alog(self, user_id: int, action: str, target: str) -> None:
        await asyncio.to_thread(self.log, user_id, action, target)
