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


@dataclass(frozen=True, slots=True)
class Decision:
    """Результат перевірки доступу: можна чи ні і ПОЯСНЕННЯ простими словами (для адмін-панелі)."""
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AclSummary:
    """Хто що може з файлом. restriction: none | all | groups | private."""
    file_uid: str
    restriction: str
    groups: tuple[str, ...]
    private_owner: int | None
    granted: tuple[int, ...]            # особисті дозволи (GRANT)
    hidden_for: tuple[int, ...]         # людина сама сховала файл (HIDE)
    removed_for_groups: tuple[str, ...]  # прибрано для групи (DEL)

    @property
    def label(self) -> str:
        """Короткий підпис для списків: що за доступ у цього файлу."""
        if self.restriction == "none":
            return "🌐 Без обмежень (бачать усі)"
        if self.restriction == "all":
            return "🌐 Відкрито для всіх груп"
        if self.restriction == "private":
            return f"🔐 Приватний (власник {self.private_owner})"
        return "🔒 Лише групи: " + ", ".join(self.groups)


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

    def explain_tier(self, user_id: int, profile: Profile | None) -> tuple[Tier, str]:
        """Рівень і ЧОМУ саме такий. Використовується в адмін-панелі."""
        tier = self.get_tier(user_id, profile)
        if user_id == self._admin_id:
            return tier, "головний адміністратор"
        if user_id in self._vip_ids:
            return tier, "оператор зі списку VIP_TELEGRAM_IDS"
        if profile is None:
            return tier, "немає в базі"
        if profile.is_vip == 1:
            return tier, "куратор: рівень видав адміністратор"
        if self._home_group and profile.group_name == self._home_group:
            return tier, f"студент основної групи {self._home_group}"
        if profile.has_paid == 1:
            return tier, "активна підписка"
        return tier, "не з основної групи і без підписки"

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
        return self.explain_file(file_path, user_group, user_id,
                                 respect_personal_hide=respect_personal_hide).allowed

    def explain_file(self, file_path: str, user_group: str | None, user_id: int,
                     *, respect_personal_hide: bool = True) -> Decision:
        """Те саме, що can_access_file, але з поясненням. Помилка = ВІДМОВА (fail-closed)."""
        try:
            return self._decide(file_path, user_group or "", user_id, respect_personal_hide)
        except Exception:
            log.exception("ACL check failed for %s; denying (fail-closed)", file_path)
            return Decision(False, "помилка перевірки прав, доступ закрито")

    async def acan_access_file(self, *args, **kwargs) -> bool:
        return await asyncio.to_thread(self.can_access_file, *args, **kwargs)

    def _decide(self, file_path: str, group: str, user_id: int, respect_hide: bool) -> Decision:
        # Нормалізуємо слеші: "a\..\b" на Linux інакше не розбивається на частини
        parts = PurePath(file_path.replace("\\", "/")).parts
        if ".." in parts:
            return Decision(False, "некоректний шлях")  # захист від path traversal
        uid = path_uid(file_path)
        is_safe_dir = SAFE_DIR in parts

        # один запит замість шести окремих з'єднань
        keys = [Keys.hide(uid, user_id), Keys.deleted(uid, group),
                Keys.simulation(user_id), Keys.grant(uid, user_id)]
        placeholders = ",".join("?" * len(keys))
        present = {r["key"] for r in self._db.query_all(
            f"SELECT key FROM bot_settings WHERE key IN ({placeholders})", keys)}
        simulated = Keys.simulation(user_id) in present
        granted = Keys.grant(uid, user_id) in present

        if respect_hide and not is_safe_dir and Keys.hide(uid, user_id) in present:
            return Decision(False, "людина сама сховала цей файл")
        if user_id == self._admin_id and not simulated:
            return Decision(True, "адміністратор бачить усе")
        if PUBLIC_DIR in parts:
            return Decision(True, f"публічна папка «{PUBLIC_DIR}»")
        if Keys.deleted(uid, group) in present:
            return Decision(False, f"файл прибрано для групи {group}")
        if user_id in self._vip_ids and not simulated:
            return Decision(True, "оператор зі списку VIP")

        row = self._db.query_one("SELECT group_name FROM file_permissions WHERE file_uid = ?", (uid,))
        if row is None or not row["group_name"]:
            return Decision(True, "обмежень немає")
        owners = [o.strip() for o in row["group_name"].split(",") if o.strip()]

        for owner in owners:
            if owner.startswith(PRIVATE_PREFIX):
                try:
                    owner_id = int(owner[len(PRIVATE_PREFIX):])
                except ValueError:
                    return Decision(False, "пошкоджений запис прав, доступ закрито")
                if user_id == owner_id:
                    return Decision(True, "власник приватного файлу")
                if granted:
                    return Decision(True, "особистий дозвіл від власника або адміна")
                return Decision(False, "приватний файл іншої людини")
        if "ALL" in owners:
            return Decision(True, "відкрито для всіх груп")
        if group in owners:
            return Decision(True, f"доступ для групи {group}")
        if granted:                       # нове в порівнянні зі старим ботом: особистий дозвіл діє й на групові файли
            return Decision(True, "особистий дозвіл адміна")
        return Decision(False, "лише для груп: " + ", ".join(owners))

    # ---------- керування правами (для адмін-панелі) ----------
    def describe_file(self, file_path: str) -> AclSummary:
        uid = path_uid(file_path)
        row = self._db.query_one("SELECT group_name FROM file_permissions WHERE file_uid = ?", (uid,))
        owners = [o.strip() for o in ((row["group_name"] if row else "") or "").split(",") if o.strip()]
        restriction, groups, private_owner = "none", (), None
        if owners:
            priv = next((o for o in owners if o.startswith(PRIVATE_PREFIX)), None)
            if priv:
                restriction = "private"
                try:
                    private_owner = int(priv[len(PRIVATE_PREFIX):])
                except ValueError:
                    private_owner = None
            elif "ALL" in owners:
                restriction = "all"
            else:
                restriction, groups = "groups", tuple(owners)

        def tail(prefix: str) -> list[str]:
            upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
            rows = self._db.query_all(
                "SELECT key FROM bot_settings WHERE key >= ? AND key < ? ORDER BY key", (prefix, upper))
            return [r["key"][len(prefix):] for r in rows]

        def ids(values: list[str]) -> tuple[int, ...]:
            return tuple(int(v) for v in values if v.lstrip("-").isdigit())

        return AclSummary(uid, restriction, groups, private_owner,
                          ids(tail(f"GRANT_{uid}_")), ids(tail(f"HIDE_{uid}_")), tuple(tail(f"DEL_{uid}_")))

    def _flag(self, key: str, on: bool) -> None:
        if on:
            self._db.execute("INSERT INTO bot_settings (key, value) VALUES (?, '1') "
                             "ON CONFLICT(key) DO UPDATE SET value = '1'", (key,))
        else:
            self._db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))

    def grant_person(self, file_path: str, user_id: int) -> None:
        self._flag(Keys.grant(path_uid(file_path), user_id), True)

    def revoke_person(self, file_path: str, user_id: int) -> None:
        self._flag(Keys.grant(path_uid(file_path), user_id), False)

    def remove_for_group(self, file_path: str, group: str, removed: bool = True) -> None:
        self._flag(Keys.deleted(path_uid(file_path), group), removed)

    def set_file_groups(self, file_path: str, groups: list[str] | None) -> None:
        """groups=None: зняти обмеження. ['ALL']: усі групи. Інакше перелік груп."""
        uid = path_uid(file_path)
        if groups is None:
            self._db.execute("DELETE FROM file_permissions WHERE file_uid = ?", (uid,))
        else:
            self.set_owner(uid, ",".join(groups), file_path)


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
