"""Точка збирання: створює всі сервіси ядра один раз, з явними залежностями."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from .access import AccessService, ProfileService
from .audit import AuditLog
from .crypto import KeyVault
from .db import Database
from .kv import KV
from .migrations import migrate
from .users import UserService


@dataclass(frozen=True)
class Core:
    settings: Settings
    db: Database
    kv: KV
    vault: KeyVault
    audit: AuditLog
    access: AccessService
    profiles: ProfileService
    users: UserService

    def close(self) -> None:
        """Коректно закрити БД (викликати при вимкненні бота)."""
        self.db.close()


def build_core(settings: Settings) -> Core:
    db = Database(settings.db_path)
    migrate(db, backup_dir=settings.data_dir / "backups")
    kv = KV(db)
    profiles = ProfileService(db, settings.vip_ids)
    return Core(
        settings=settings,
        db=db,
        kv=kv,
        vault=KeyVault(kv, settings.master_key, wrap=settings.wrap_user_keys),
        audit=AuditLog(db, settings.timezone),
        access=AccessService(db, admin_id=settings.admin_id, vip_ids=settings.vip_ids,
                             home_group=settings.home_group),
        profiles=profiles,
        users=UserService(db, profiles),
    )