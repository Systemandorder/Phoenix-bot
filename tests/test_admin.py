"""Тести адмін-сервісу. Головне, що доводять. Змінювати права може лише головний адмін,
зміни збігаються з командами старого бота, а помилка завжди зрозуміла і нічого не ламає."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from phoenix.core.access import Tier
from phoenix.core.admin import AdminError, AdminService
from phoenix.core.bootstrap import build_core

from test_core import make_settings

NOW = datetime(2026, 11, 10, 12, 0, 0)
ADMIN = 1


class AdminCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))     # home_group = "G"
        self.addCleanup(self.core.close)
        self.adm = AdminService(self.core.db, self.core.access, self.core.profiles, self.core.audit,
                                admin_id=ADMIN, now=lambda: NOW)
        self.core.users.register(10, "Іван", "Іванов", "G", None)         # студент основної групи
        self.core.users.register(11, "Анна", "Коваль", "XY-1", None)      # чужа група, без підписки
        self.core.users.register(12, "Петро", "Сидоренко", "XY-1", None)

    def row(self, uid):
        return self.core.db.query_one("SELECT * FROM users WHERE user_id = ?", (uid,))

    def tier(self, uid) -> Tier:
        self.core.profiles.invalidate(uid)
        profile = asyncio.run(self.core.profiles.get(uid))
        return self.core.access.get_tier(uid, profile)

    def last_audit(self):
        r = self.core.db.query_one("SELECT user_id, action FROM audit_logs ORDER BY id DESC LIMIT 1")
        return (r["user_id"], r["action"]) if r else None


class OnlyAdminTests(AdminCase):
    def test_nobody_else_can_change_anything(self):
        calls = [lambda a: self.adm.promote(a, 11), lambda a: self.adm.demote(a, 11),
                 lambda a: self.adm.grant_member(a, 11, 1), lambda a: self.adm.expire(a, 11),
                 lambda a: self.adm.grant_group(a, "XY-1", 1), lambda a: self.adm.set_group(a, 11, "Y"),
                 lambda a: self.adm.set_clicks(a, 11, 50)]
        before = tuple(self.row(11))
        for actor in (10, 11, 999, 0, -1):                       # студент, сам цільовий, чужий, дивні id
            for call in calls:
                with self.subTest(actor=actor):
                    with self.assertRaises(AdminError):
                        call(actor)
        self.assertEqual(tuple(self.row(11)), before)
        self.assertIsNone(self.last_audit())                     # відмови нічого не пишуть у журнал

    def test_main_admin_cannot_be_targeted(self):
        self.core.db.execute("INSERT INTO users (user_id, first_name, group_name) VALUES (1, 'Адм', 'G')")
        for call in (lambda: self.adm.promote(ADMIN, ADMIN), lambda: self.adm.expire(ADMIN, ADMIN),
                     lambda: self.adm.set_group(ADMIN, ADMIN, "Y")):
            with self.assertRaises(AdminError):
                call()

    def test_unknown_user_is_rejected(self):
        with self.assertRaises(AdminError):
            self.adm.promote(ADMIN, 555)


class PromoteTests(AdminCase):
    def test_promote_makes_curator_level_3_and_logs(self):
        self.assertEqual(self.tier(11), Tier.GUEST)
        self.adm.promote(ADMIN, 11)
        self.assertEqual(self.tier(11), Tier.MODERATOR)
        r = self.row(11)
        self.assertEqual((r["is_vip"], r["has_paid"], r["subscription_end"]), (1, 0, None))
        self.assertEqual(self.last_audit(), (ADMIN, "ПРИЗНАЧЕННЯ_МОДЕРАТОРА"))

    def test_promote_twice_and_demote_not_curator_give_clear_errors(self):
        self.adm.promote(ADMIN, 11)
        with self.assertRaises(AdminError):
            self.adm.promote(ADMIN, 11)
        with self.assertRaises(AdminError):
            self.adm.demote(ADMIN, 12)

    def test_demote_returns_to_rules_of_group(self):
        self.adm.promote(ADMIN, 10)
        self.adm.demote(ADMIN, 10)
        self.assertEqual(self.tier(10), Tier.MEMBER)             # студент основної групи
        self.assertEqual(self.row(10)["is_vip"], 0)


class SubscriptionTests(AdminCase):
    def test_grant_member_sets_level_2_clicks_and_end_date(self):
        self.adm.grant_member(ADMIN, 11, 2)
        r = self.row(11)
        self.assertEqual((r["has_paid"], r["free_clicks"], r["subscription_end"]), (1, 20, "2027-01-09 12:00:00"))
        self.assertEqual(self.tier(11), Tier.MEMBER)

    def test_grant_member_turns_curator_into_student(self):
        self.adm.promote(ADMIN, 11)
        self.adm.grant_member(ADMIN, 11, 1)
        self.assertEqual(self.row(11)["is_vip"], 0)

    def test_month_limits(self):
        for bad in (0, -1, 121):
            with self.assertRaises(AdminError):
                self.adm.grant_member(ADMIN, 11, bad)
        self.adm.grant_member(ADMIN, 11, 120)

    def test_expire_closes_subscription(self):
        self.adm.grant_member(ADMIN, 11, 1)
        self.adm.expire(ADMIN, 11)
        r = self.row(11)
        self.assertEqual((r["has_paid"], r["free_clicks"], r["subscription_end"]), (0, 0, "2026-11-09 12:00:00"))
        self.assertEqual(self.tier(11), Tier.GUEST)


class GroupTests(AdminCase):
    def test_grant_group_skips_curators(self):
        self.adm.promote(ADMIN, 12)
        text, n = self.adm.grant_group(ADMIN, "XY-1", 1)
        self.assertEqual(n, 1)                                    # лише Анна, куратора не чіпаємо
        self.assertEqual(self.row(11)["has_paid"], 1)
        self.assertEqual(self.row(12)["is_vip"], 1)
        self.assertEqual(self.last_audit(), (ADMIN, "ВИДАЧА_РІВНЯ_ГРУПІ"))

    def test_grant_group_errors(self):
        for bad in ("", "<b>", "ФБ-56; DROP"):
            with self.assertRaises(AdminError):
                self.adm.grant_group(ADMIN, bad, 1)
        with self.assertRaises(AdminError):
            self.adm.grant_group(ADMIN, "ПУСТА-1", 1)             # групи нема в базі

    def test_set_group_validates_and_applies(self):
        self.adm.set_group(ADMIN, 11, "фі-51")
        self.assertEqual(self.row(11)["group_name"], "ФІ-51")
        with self.assertRaises(AdminError):
            self.adm.set_group(ADMIN, 11, "дуже довга назва групи")

    def test_set_clicks_bounds(self):
        self.adm.set_clicks(ADMIN, 11, 7)
        self.assertEqual(self.row(11)["free_clicks"], 7)
        for bad in (-1, 100001):
            with self.assertRaises(AdminError):
                self.adm.set_clicks(ADMIN, 11, bad)


class ReadTests(AdminCase):
    def test_counts_and_curators(self):
        self.adm.promote(ADMIN, 11)
        users, groups, curators = self.adm.counts()
        self.assertEqual((users, groups, curators), (3, 2, 1))

    def test_explain_tier_reasons(self):
        self.adm.promote(ADMIN, 11)
        self.assertIn("куратор", self.adm.tier_of(self.adm.get_user(11))[1])
        self.assertIn("основної групи", self.adm.tier_of(self.adm.get_user(10))[1])
        self.assertIn("без підписки", self.adm.tier_of(self.adm.get_user(12))[1])


class AsyncCallTests(AdminCase):
    def test_async_wrapper_runs_allowed_methods(self):
        msg = asyncio.run(self.adm.acall("promote", ADMIN, 11))
        self.assertIn("куратор", msg)

    def test_async_wrapper_refuses_private_or_unknown_names(self):
        for name in ("_db", "_require_admin", "__class__", "nonexistent", "execute"):
            with self.subTest(name=name):
                with self.assertRaises(AdminError):
                    asyncio.run(self.adm.acall(name, ADMIN))


if __name__ == "__main__":
    unittest.main()
