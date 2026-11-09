"""Тести ядра адміністрування: рівні, куратори, підписки, групи, пошук, журнал."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from phoenix.core.access import Tier
from phoenix.core.admin import AdminError, AdminService
from phoenix.core.bootstrap import build_core

from test_core import make_settings

ADMIN = 1
FIXED_NOW = datetime(2026, 11, 9, 12, 0, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        c = self.core
        self.adm = AdminService(c.db, c.access, c.profiles, c.audit, admin_id=ADMIN, now=lambda: FIXED_NOW)
        self.add(10, "Анна", "Коваль", "ФБ-56", "anna")
        self.add(11, "Іван", "Іванов", "ФБ-55")
        self.add(12, "Петро", "Сидоренко", "ФБ-55", "petro")
        self.add(13, "Без", "Групи", None)

    def add(self, uid, first, last, group, username=None):
        self.core.db.execute("INSERT INTO users (user_id, first_name, last_name, group_name, username) "
                             "VALUES (?,?,?,?,?)", (uid, first, last, group, username))

    def row(self, uid):
        return self.core.db.query_one("SELECT * FROM users WHERE user_id=?", (uid,))

    def last_audit(self):
        return self.core.db.query_one("SELECT user_id, action, target FROM audit_logs ORDER BY id DESC")


class ReadTests(Base):
    def test_counts_and_groups(self):
        n, groups, curators = self.adm.counts()
        self.assertEqual((n, curators), (4, 0))
        info = {g.name: g for g in self.adm.list_groups()}
        self.assertEqual(info["ФБ-55"].users, 2)
        self.assertEqual(info["—"].users, 1)           # без групи

    def test_list_users_paging_and_group_filter(self):
        rows, total = self.adm.list_users(None, 0, 3)
        self.assertEqual((len(rows), total), (3, 4))
        rows2, _ = self.adm.list_users(None, 3, 3)
        self.assertEqual(len(rows2), 1)
        rows, total = self.adm.list_users("ФБ-55")
        self.assertEqual((total, {r.user_id for r in rows}), (2, {11, 12}))
        rows, total = self.adm.list_users("—")
        self.assertEqual([r.user_id for r in rows], [13])

    def test_search_is_case_insensitive_for_cyrillic(self):
        self.assertEqual([u.user_id for u in self.adm.search("КОВАЛЬ")], [10])
        self.assertEqual([u.user_id for u in self.adm.search("@petro")], [12])
        self.assertEqual([u.user_id for u in self.adm.search("11")], [11])
        self.assertEqual(self.adm.search("   "), [])

    def test_tier_reason(self):
        u = self.adm.get_user(10)
        tier, reason = self.adm.tier_of(u)
        self.assertEqual(tier, Tier.GUEST)              # home_group у тестах "G"
        self.assertIn("без підписки", reason)


class PromoteTests(Base):
    def test_promote_makes_curator_like_old_bot(self):
        self.core.db.execute("UPDATE users SET has_paid=1, subscription_end='2030-01-01 00:00:00' WHERE user_id=10")
        self.adm.promote(ADMIN, 10)
        r = self.row(10)
        self.assertEqual((r["is_vip"], r["has_paid"], r["subscription_end"]), (1, 0, None))
        self.assertEqual(self.adm.curators()["ФБ-56"][0].user_id, 10)
        self.assertEqual(self.last_audit()["action"], "ПРИЗНАЧЕННЯ_МОДЕРАТОРА")

    def test_promote_changes_real_tier_immediately(self):
        import asyncio
        self.assertEqual(self.core.access.get_tier(10, asyncio.run(self.core.profiles.get(10))), Tier.GUEST)
        self.adm.promote(ADMIN, 10)
        self.assertEqual(self.core.access.get_tier(10, asyncio.run(self.core.profiles.get(10))), Tier.MODERATOR)

    def test_demote(self):
        self.adm.promote(ADMIN, 10)
        self.adm.demote(ADMIN, 10)
        self.assertEqual(self.row(10)["is_vip"], 0)
        with self.assertRaises(AdminError):
            self.adm.demote(ADMIN, 10)

    def test_only_admin_can_change_anything(self):
        for call in (lambda: self.adm.promote(99, 10), lambda: self.adm.demote(99, 10),
                     lambda: self.adm.grant_member(99, 10, 1), lambda: self.adm.expire(99, 10),
                     lambda: self.adm.grant_group(99, "ФБ-55", 1), lambda: self.adm.set_group(99, 10, "ФБ-55"),
                     lambda: self.adm.set_clicks(99, 10, 5)):
            with self.assertRaises(AdminError):
                call()
        self.assertEqual(self.row(10)["is_vip"], 0)

    def test_cannot_target_admin_or_missing_user(self):
        self.add(ADMIN, "Адм", "Адмін", "ФБ-56")
        with self.assertRaises(AdminError):
            self.adm.promote(ADMIN, ADMIN)
        with self.assertRaises(AdminError):
            self.adm.promote(ADMIN, 777)


class SubscriptionTests(Base):
    def test_grant_member_like_approve(self):
        self.adm.promote(ADMIN, 10)
        self.adm.grant_member(ADMIN, 10, 2)
        r = self.row(10)
        self.assertEqual((r["has_paid"], r["is_vip"], r["free_clicks"]), (1, 0, 20))
        self.assertEqual(r["subscription_end"], "2027-01-08 12:00:00")   # +60 днів від FIXED_NOW
        self.assertEqual(self.last_audit()["action"], "СХВАЛЕННЯ_ОПЛАТИ")

    def test_months_validated(self):
        for bad in (0, -1, 121):
            with self.assertRaises(AdminError):
                self.adm.grant_member(ADMIN, 10, bad)

    def test_expire_like_old_bot(self):
        self.adm.grant_member(ADMIN, 10, 1)
        self.adm.expire(ADMIN, 10)
        r = self.row(10)
        self.assertEqual((r["has_paid"], r["free_clicks"], r["subscription_end"]), (0, 0, "2026-11-08 12:00:00"))

    def test_grant_group_skips_curators(self):
        self.adm.promote(ADMIN, 11)
        text, n = self.adm.grant_group(ADMIN, "фб-55", 1)
        self.assertEqual(n, 1)
        self.assertEqual(self.row(12)["has_paid"], 1)
        self.assertEqual(self.row(11)["has_paid"], 0)       # куратор не чіпається
        self.assertEqual(self.row(10)["has_paid"], 0)       # інша група

    def test_grant_group_errors(self):
        with self.assertRaises(AdminError):
            self.adm.grant_group(ADMIN, "ФБ-99", 1)
        with self.assertRaises(AdminError):
            self.adm.grant_group(ADMIN, "<x>", 1)


class EditTests(Base):
    def test_set_group_validates_and_logs(self):
        self.adm.set_group(ADMIN, 10, "фі-51")
        self.assertEqual(self.row(10)["group_name"], "ФІ-51")
        self.assertIn("ФБ-56 -> ФІ-51", self.last_audit()["target"])
        with self.assertRaises(AdminError):
            self.adm.set_group(ADMIN, 10, "ФБ; DROP")

    def test_set_clicks_range(self):
        self.adm.set_clicks(ADMIN, 10, 7)
        self.assertEqual(self.row(10)["free_clicks"], 7)
        for bad in (-1, 100001):
            with self.assertRaises(AdminError):
                self.adm.set_clicks(ADMIN, 10, bad)

    def test_recent_log_newest_first(self):
        self.adm.promote(ADMIN, 10)
        self.adm.set_clicks(ADMIN, 10, 3)
        rows, total = self.adm.recent_log(0, 10)
        self.assertEqual((rows[0].action, total), ("ЗМІНА_КЛІКІВ", 2))


if __name__ == "__main__":
    unittest.main()