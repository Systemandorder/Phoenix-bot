"""Користувачі: реєстрація, перевірка імені та групи, оновлення username.

Головна відмінність від старого бота: запис робиться через UPSERT, який НЕ чіпає
is_vip, has_paid, free_clicks і дату підписки. Старий `INSERT OR REPLACE` стирав їх
(після /restart людина втрачала оплату і VIP-статус).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import Enum

from .access import ProfileService
from .db import Database

# Літери (будь-якої мови), апостроф, дефіс, пробіл. Починається з літери, до 40 символів.
_NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’ʼ\- ]){0,39}$")
# Група: ФБ-56, ФІ-51, КВ-42мп... Великі літери, цифри, дефіс; 2-12 символів.
_GROUP_RE = re.compile(r"^[A-ZА-ЯІЇЄҐ0-9][A-ZА-ЯІЇЄҐ0-9\-]{1,11}$")


def clean_name(raw: str | None) -> str | None:
    """Прибирає зайві пробіли. None, якщо це не схоже на імʼя або прізвище."""
    text = " ".join((raw or "").split())
    return text if _NAME_RE.match(text) else None


def clean_group(raw: str | None) -> str | None:
    """Назва групи у верхньому регістрі. None, якщо формат дивний."""
    text = "".join((raw or "").split()).upper()
    return text if _GROUP_RE.match(text) else None


class RegisterStatus(Enum):
    OK = "ok"
    DUPLICATE_NAME = "duplicate_name"


@dataclass(frozen=True, slots=True)
class RegisterResult:
    status: RegisterStatus

    @property
    def ok(self) -> bool:
        return self.status is RegisterStatus.OK


class UserService:
    def __init__(self, db: Database, profiles: ProfileService) -> None:
        self._db = db
        self._profiles = profiles

    def register(self, user_id: int, first_name: str, last_name: str, group: str,
                 username: str | None) -> RegisterResult:
        """Перевірка дубліката і запис в одній транзакції."""
        with self._db.tx() as conn:
            clash = conn.execute(
                "SELECT user_id FROM users WHERE first_name = ? AND last_name = ? AND user_id != ?",
                (first_name, last_name, user_id)).fetchone()
            if clash:
                return RegisterResult(RegisterStatus.DUPLICATE_NAME)
            conn.execute(
                "INSERT INTO users (user_id, first_name, last_name, group_name, username) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "first_name = excluded.first_name, last_name = excluded.last_name, "
                "group_name = excluded.group_name, "
                "username = COALESCE(excluded.username, users.username)",
                (user_id, first_name, last_name, group, username))
        self._profiles.invalidate(user_id)
        return RegisterResult(RegisterStatus.OK)

    def touch_username(self, user_id: int, username: str | None) -> None:
        """Заповнює username, якщо він у базі порожній (як у старому боті)."""
        if not username:
            return
        self._db.execute("UPDATE users SET username = ? WHERE user_id = ? AND username IS NULL",
                         (username, user_id))

    # ---- async-версії: SQLite не блокує цикл подій бота ----
    async def aregister(self, *args, **kwargs) -> RegisterResult:
        return await asyncio.to_thread(self.register, *args, **kwargs)

    async def atouch_username(self, user_id: int, username: str | None) -> None:
        await asyncio.to_thread(self.touch_username, user_id, username)
