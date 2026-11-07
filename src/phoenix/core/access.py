"""Access control: user tiers and per-file ACL.

Storage is identical to the legacy bot (bot_settings keys + file_permissions),
so old and new bots can share one DB during the transition.
Behaviour change vs legacy: FAIL-CLOSED on any error (legacy returned True).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import PurePath

from .db import Database
from .kv import Keys

log = logging.getLogger(__name__)

PUBLIC_DIR = "Архів знань"
SAFE_DIR = ".safe_files"
PRIVATE_PREFIX = "PRIVATE_"


class Tier(IntEnum):
    GUEST = 1
    MEMBER = 2
    MODERATOR = 3
    ADMIN = 4


@dataclass(frozen=True, slots=True)
class Profile:
    group_name: str | None
    is_vip: int
    has_paid: int
    free_clicks: int
    first_name: str | None
    last_name: str | None


def path_uid(file_path: str) -> str:
    """Legacy-compatible file id (md5[:8]). Not a security boundary."""
    return hashlib.md5(file_path.encode(), usedforsecurity=False).hexdigest()[:8]


class AccessService:
    def __init__(self, db: Database, *, admin_id: int,
                 vip_ids: frozenset[int] = frozenset(), home_group: str = "") -> None:
        self._db = db
        self._admin_id = admin_id
        self._vip_ids = vip_ids
        self._home_group = home_group

    # ---------- tiers ----------
    def get_tier(self, user_id: int, profile: Profile | None, *, mock_tier: int | None = None) -> Tier:
        if user_id == self._admin_id:
            if mock_tier is not None:  # simulation is an admin-only feature
                return Tier(max(1, min(4, int(mock_tier))))
            return Tier.ADMIN
        if user_id in self._vip_ids:
            return Tier.MEMBER
        if profile is None:
            return Tier.GUEST
        if profile.is_vip == 1:
            return Tier.MODERATOR
        if (self._home_group and profile.group_name == self._home_group) or profile.has_paid == 1:
            return Tier.MEMBER
        return Tier.GUEST

    # ---------- file ACL ----------
    def set_owner(self, file_uid: str, owners: str, file_path: str = "") -> None:
        """owners: 'ALL' | 'GROUP1,GROUP2' | 'PRIVATE_<user_id>'."""
        self._db.execute(
            "INSERT INTO file_permissions (file_uid, file_path, group_name) VALUES (?, ?, ?) "
            "ON CONFLICT(file_uid) DO UPDATE SET group_name = excluded.group_name, "
            "file_path = COALESCE(NULLIF(excluded.file_path, ''), file_permissions.file_path)",
            (file_uid, file_path, owners),
        )

    def can_access_file(self, file_path: str, user_group: str | None, user_id: int,
                        *, respect_personal_hide: bool = True) -> bool:
        try:
            return self._can_access(file_path, user_group or "", user_id, respect_personal_hide)
        except Exception:
            log.exception("ACL check failed for %s; denying (fail-closed)", file_path)
            return False

    async def acan_access_file(self, *args, **kwargs) -> bool:
        return await asyncio.to_thread(self.can_access_file, *args, **kwargs)

    def _can_access(self, file_path: str, group: str, user_id: int, respect_hide: bool) -> bool:
           # Нормалізуємо слеші: "a\..\b" на Linux інакше не розбивається на частини
        parts = PurePath(file_path.replace("\\", "/")).parts
        if ".." in parts:
            return False  # захист від path traversal
        uid = path_uid(file_path)
        is_safe_dir = SAFE_DIR in parts

        # one query instead of up to six separate connections
        keys = [Keys.hide(uid, user_id), Keys.deleted(uid, group),
                Keys.simulation(user_id), Keys.grant(uid, user_id)]
        placeholders = ",".join("?" * len(keys))
        present = {r["key"] for r in self._db.query_all(
            f"SELECT key FROM bot_settings WHERE key IN ({placeholders})", keys)}
        simulated = Keys.simulation(user_id) in present

        if respect_hide and not is_safe_dir and Keys.hide(uid, user_id) in present:
            return False
        if user_id == self._admin_id and not simulated:
            return True
        if PUBLIC_DIR in parts:
            return True
        if Keys.deleted(uid, group) in present:
            return False
        if user_id in self._vip_ids and not simulated:
            return True

        row = self._db.query_one("SELECT group_name FROM file_permissions WHERE file_uid = ?", (uid,))
        if row is None or not row["group_name"]:
            return True  # no restriction recorded
        owners = [o.strip() for o in row["group_name"].split(",") if o.strip()]

        for owner in owners:
            if owner.startswith(PRIVATE_PREFIX):
                try:
                    owner_id = int(owner[len(PRIVATE_PREFIX):])
                except ValueError:
                    return False  # corrupted record -> closed
                return user_id == owner_id or Keys.grant(uid, user_id) in present
        return group in owners or "ALL" in owners


class ProfileService:
    """Async user-profile lookup with a small TTL cache."""

    _SQL = ("SELECT group_name, is_vip, has_paid, free_clicks, first_name, last_name "
            "FROM users WHERE user_id = ?")

    def __init__(self, db: Database, vip_ids: frozenset[int] = frozenset(),
                 ttl: float = 60.0, max_size: int = 5000) -> None:
        self._db, self._vip_ids, self._ttl, self._max = db, vip_ids, ttl, max_size
        self._cache: dict[int, tuple[Profile | None, float]] = {}

    async def get(self, user_id: int) -> Profile | None:
        now = time.monotonic()
        hit = self._cache.get(user_id)
        if hit and now - hit[1] < self._ttl:
            return hit[0]
        row = await self._db.aquery_one(self._SQL, (user_id,))
        profile = Profile(*tuple(row)) if row else None
        if profile and user_id in self._vip_ids:
            profile = replace(profile, is_vip=1)
        if len(self._cache) >= self._max:
            self._cache.clear()
        self._cache[user_id] = (profile, now)
        return profile

    def invalidate(self, user_id: int) -> None:
        """Call after registration, payment, promote, group change."""
        self._cache.pop(user_id, None)
