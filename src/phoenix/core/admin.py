"""Керування користувачами для адміністратора: рівні, куратори, групи, підписки.

Усі зміни:
  * тільки від головного адміністратора (інакше помилка, навіть якщо хтось підробить кнопку);
  * однаково змінюють базу, як це робили команди старого бота (/promote, /approve, /grantgroup, /expire);
  * скидають кеш профілю і пишуть рядок у журнал дій (назви дій як у старому боті).

«Куратор» тут це модератор (рівень 3, is_vip=1): людина, якій адміністратор видав права.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .access import AccessService, Profile, ProfileService, Tier
from .audit import AuditLog
from .db import Database
from .users import clean_group

NO_GROUP = "—"
DATE_FMT = "%Y-%m-%d %H:%M:%S"
GRANT_CLICKS = 20              # стільки безкоштовних кліків давав старий /approve
MAX_MONTHS = 120


class AdminError(Exception):
    """Зрозуміла людині причина відмови (показується в боті як є)."""


@dataclass(frozen=True, slots=True)
class UserRow:
    user_id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    group_name: str | None
    is_vip: int
    has_paid: int
    subscription_end: str | None
    free_clicks: int

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name) if p) or f"ID {self.user_id}"

    @property
    def group(self) -> str:
        return self.group_name or NO_GROUP

    def profile(self) -> Profile:
        return Profile(self.group_name, self.is_vip, self.has_paid, self.free_clicks,
                       self.first_name, self.last_name)


@dataclass(frozen=True, slots=True)
class GroupInfo:
    name: str
    users: int
    curators: int
    paid: int


@dataclass(frozen=True, slots=True)
class AuditRow:
    id: int
    user_id: int | None
    action: str | None
    target: str | None
    timestamp: str | None


_COLS = "user_id, first_name, last_name, username, group_name, is_vip, has_paid, subscription_end, free_clicks"


def _row(r) -> UserRow:
    return UserRow(r["user_id"], r["first_name"], r["last_name"], r["username"], r["group_name"],
                   r["is_vip"] or 0, r["has_paid"] or 0, r["subscription_end"], r["free_clicks"] or 0)

_ASYNC_ALLOWED = frozenset({
    "get_user", "counts", "list_groups", "list_users", "curators", "search", "tier_of", "recent_log",
    "promote", "demote", "grant_member", "expire", "grant_group", "set_group", "set_clicks",
})
class AdminService:
    def __init__(self, db: Database, access: AccessService, profiles: ProfileService, audit: AuditLog,
                 *, admin_id: int, now: Callable[[], datetime] = datetime.now) -> None:
        self._db, self._access, self._profiles, self._audit = db, access, profiles, audit
        self._admin_id, self._now = admin_id, now

    # ================= читання =================
    def get_user(self, user_id: int) -> UserRow | None:
        r = self._db.query_one(f"SELECT {_COLS} FROM users WHERE user_id = ?", (user_id,))
        return _row(r) if r else None

    def counts(self) -> tuple[int, int, int]:
        """(користувачів, груп, кураторів)"""
        r = self._db.query_one("SELECT COUNT(*) n, COUNT(DISTINCT group_name) g, "
                               "COALESCE(SUM(is_vip = 1), 0) c FROM users")
        return r["n"], r["g"], r["c"]

    def list_groups(self) -> list[GroupInfo]:
        rows = self._db.query_all(
            "SELECT group_name, COUNT(*) n, COALESCE(SUM(is_vip = 1), 0) c, COALESCE(SUM(has_paid = 1), 0) p "
            "FROM users GROUP BY group_name ORDER BY group_name")
        return [GroupInfo(r["group_name"] or NO_GROUP, r["n"], r["c"], r["p"]) for r in rows]

    def list_users(self, group: str | None = None, offset: int = 0, limit: int = 8) -> tuple[list[UserRow], int]:
        """Сторінка користувачів (усі або однієї групи) і загальна кількість."""
        where, params = "", ()
        if group == NO_GROUP:
            where = "WHERE group_name IS NULL OR group_name = ''"
        elif group:
            where, params = "WHERE group_name = ?", (group,)
        total = self._db.query_one(f"SELECT COUNT(*) n FROM users {where}", params)["n"]
        rows = self._db.query_all(
            f"SELECT {_COLS} FROM users {where} ORDER BY group_name, last_name, first_name LIMIT ? OFFSET ?",
            (*params, limit, max(0, offset)))
        return [_row(r) for r in rows], total

    def curators(self) -> dict[str, list[UserRow]]:
        """Куратори (рівень 3) по групах."""
        rows = self._db.query_all(f"SELECT {_COLS} FROM users WHERE is_vip = 1 "
                                  "ORDER BY group_name, last_name, first_name")
        out: dict[str, list[UserRow]] = {}
        for r in rows:
            u = _row(r)
            out.setdefault(u.group, []).append(u)
        return out

    def search(self, query: str, limit: int = 10) -> list[UserRow]:
        """Пошук за прізвищем, імʼям, @username або ID. Регістр не важливий (і для кирилиці теж)."""
        q = (query or "").strip().lstrip("@").casefold()
        if not q:
            return []
        if q.lstrip("-").isdigit():
            hit = self.get_user(int(q))
            if hit:
                return [hit]
        out = []
        for r in self._db.query_all(f"SELECT {_COLS} FROM users ORDER BY last_name, first_name"):
            u = _row(r)
            hay = " ".join(filter(None, [u.last_name, u.first_name, u.username, u.group_name])).casefold()
            if q in hay:
                out.append(u)
                if len(out) >= limit:
                    break
        return out

    def tier_of(self, u: UserRow) -> tuple[Tier, str]:
        """Рівень людини і причина. Для адміна поза базою теж працює."""
        return self._access.explain_tier(u.user_id, u.profile())

    def recent_log(self, offset: int = 0, limit: int = 15) -> tuple[list[AuditRow], int]:
        total = self._db.query_one("SELECT COUNT(*) n FROM audit_logs")["n"]
        rows = self._db.query_all("SELECT id, user_id, action, target, timestamp FROM audit_logs "
                                  "ORDER BY id DESC LIMIT ? OFFSET ?", (limit, max(0, offset)))
        return [AuditRow(r["id"], r["user_id"], r["action"], r["target"], r["timestamp"]) for r in rows], total

    # ================= зміни (лише адмін) =================
    def _require_admin(self, actor: int) -> None:
        if actor != self._admin_id:
            raise AdminError("⛔ Це може лише головний адміністратор.")

    def _target(self, user_id: int) -> UserRow:
        u = self.get_user(user_id)
        if u is None:
            raise AdminError("❌ Користувача не знайдено.")
        if user_id == self._admin_id:
            raise AdminError("ℹ️ Головний адміністратор уже має найвищий рівень.")
        return u

    def _done(self, actor: int, target: int, action: str, details: str) -> None:
        self._profiles.invalidate(target)
        self._audit.log(actor, action, details)

    @staticmethod
    def _check_months(months: int) -> None:
        if not 1 <= months <= MAX_MONTHS:
            raise AdminError(f"⚠️ Місяців має бути від 1 до {MAX_MONTHS}.")

    def promote(self, actor: int, target: int) -> str:
        """Зробити куратором (рівень 3). Як /promote: підписка не потрібна й не спливає."""
        self._require_admin(actor)
        u = self._target(target)
        if u.is_vip == 1:
            raise AdminError("ℹ️ Це вже куратор.")
        self._db.execute("UPDATE users SET is_vip = 1, subscription_end = NULL, has_paid = 0 WHERE user_id = ?",
                         (target,))
        self._done(actor, target, "ПРИЗНАЧЕННЯ_МОДЕРАТОРА", f"Юзер {target} -> Рівень 3")
        return f"🎓 {u.full_name} тепер куратор (рівень 3)."

    def demote(self, actor: int, target: int) -> str:
        """Зняти куратора: лишається звичайним студентом/гостем за правилами групи й підписки."""
        self._require_admin(actor)
        u = self._target(target)
        if u.is_vip != 1:
            raise AdminError("ℹ️ Ця людина не куратор.")
        self._db.execute("UPDATE users SET is_vip = 0 WHERE user_id = ?", (target,))
        self._done(actor, target, "ЗНЯТТЯ_МОДЕРАТОРА", f"Юзер {target} -> Рівень 3 знято")
        return f"⬇️ {u.full_name} більше не куратор."

    def grant_member(self, actor: int, target: int, months: int = 1) -> str:
        """Видати рівень 2 на N місяців. Як /approve: 20 кліків, куратор стає студентом (is_vip=0)."""
        self._require_admin(actor)
        self._check_months(months)
        u = self._target(target)
        end = (self._now() + timedelta(days=30 * months)).strftime(DATE_FMT)
        self._db.execute("UPDATE users SET has_paid = 1, is_vip = 0, subscription_end = ?, free_clicks = ? "
                         "WHERE user_id = ?", (end, GRANT_CLICKS, target))
        self._done(actor, target, "СХВАЛЕННЯ_ОПЛАТИ", f"Юзер {target} -> Оператор (Рівень 2), {months} місяців")
        return f"✅ {u.full_name}: рівень 2 на {months} міс., до {end[:10]}."

    def expire(self, actor: int, target: int) -> str:
        """Примусово завершити підписку. Як /expire: has_paid=0, кліки 0."""
        self._require_admin(actor)
        u = self._target(target)
        yesterday = (self._now() - timedelta(days=1)).strftime(DATE_FMT)
        self._db.execute("UPDATE users SET subscription_end = ?, has_paid = 0, free_clicks = 0 WHERE user_id = ?",
                         (yesterday, target))
        self._done(actor, target, "ПРИМУСОВЕ_ЗАВЕРШЕННЯ_ПІДПИСКИ", f"Юзер {target}")
        return f"⏳ Підписку {u.full_name} завершено."

    def grant_group(self, actor: int, group: str, months: int = 1) -> tuple[str, int]:
        """Рівень 2 усій групі, куратори не змінюються. Як /grantgroup."""
        self._require_admin(actor)
        self._check_months(months)
        group = (clean_group(group) or "")
        if not group:
            raise AdminError("⚠️ Некоректна назва групи.")
        n = self._db.query_one("SELECT COUNT(*) n FROM users WHERE group_name = ?", (group,))["n"]
        if n == 0:
            raise AdminError(f"📭 У групі {group} немає зареєстрованих студентів.")
        end = (self._now() + timedelta(days=30 * months)).strftime(DATE_FMT)
        cur = self._db.execute("UPDATE users SET has_paid = 1, subscription_end = ? "
                               "WHERE group_name = ? AND is_vip = 0", (end, group))
        for r in self._db.query_all("SELECT user_id FROM users WHERE group_name = ?", (group,)):
            self._profiles.invalidate(r["user_id"])
        self._audit.log(actor, "ВИДАЧА_РІВНЯ_ГРУПІ", f"Група {group}, {months} місяців, {cur.rowcount} осіб")
        return f"✅ Група {group}: рівень 2 на {months} міс. для {cur.rowcount} осіб.", cur.rowcount

    def set_group(self, actor: int, target: int, group: str) -> str:
        self._require_admin(actor)
        u = self._target(target)
        new = clean_group(group)
        if new is None:
            raise AdminError("⚠️ Назва групи: великі літери, цифри й дефіс, 2-12 символів (наприклад ФБ-56).")
        self._db.execute("UPDATE users SET group_name = ? WHERE user_id = ?", (new, target))
        self._done(actor, target, "ЗМІНА_ГРУПИ", f"Юзер {target}: {u.group} -> {new}")
        return f"🔁 {u.full_name}: група {u.group} -> {new}."

    def set_clicks(self, actor: int, target: int, clicks: int) -> str:
        self._require_admin(actor)
        u = self._target(target)
        if not 0 <= clicks <= 100000:
            raise AdminError("⚠️ Кліків має бути від 0 до 100000.")
        self._db.execute("UPDATE users SET free_clicks = ? WHERE user_id = ?", (clicks, target))
        self._done(actor, target, "ЗМІНА_КЛІКІВ", f"Юзер {target}: {clicks} кліків")
        return f"🧮 {u.full_name}: безкоштовних кліків {clicks}."

    # ---- async-версії: база не блокує бота ----
    async def acall(self, name: str, *args):
        """Виконує дію в окремому потоці. Дозволені лише імена з білого списку, бо назва може
        прийти з кнопки, і за нею не можна запускати будь-який внутрішній метод."""
        if name not in _ASYNC_ALLOWED:
            raise AdminError("⚠️ Невідома дія.")
        return await asyncio.to_thread(getattr(self, name), *args)
