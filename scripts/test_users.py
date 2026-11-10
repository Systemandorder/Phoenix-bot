"""Тести UserService: реєстрація, UPSERT без втрати оплати/VIP, дублікати, перевірка імен."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from phoenix.core.bootstrap import build_core
from phoenix.core.users import RegisterStatus, clean_group, clean_name

from test_core import make_settings


class CleanTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(clean_name("  Олексій  "), "Олексій")
        self.assertEqual(clean_name("Анна   Марія"), "Анна Марія")
        self.assertEqual(clean_name("Олексій\nДругий"), "Олексій Другий")  # переноси стають пробілом
        self.assertEqual(clean_name("О'Коннор"), "О'Коннор")
        self.assertEqual(clean_name("Петренко-Іваненко"), "Петренко-Іваненко")
        for bad in ("", "   ", None, "123", "<b>x</b>", "-Іван", "A" * 41, "Ivan_1"):
            self.assertIsNone(clean_name(bad), bad)

    def test_groups(self):
        self.assertEqual(clean_group("фб-56"), "ФБ-56")
        self.assertEqual(clean_group(" фі 51 "), "ФІ51")
        for bad in ("", "Ф", "ФБ-56; DROP TABLE", "<script>", "A" * 13, None):
            self.assertIsNone(clean_group(bad), bad)


class UserServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        self.u = self.core.users

    def row(self, uid):
        return self.core.db.query_one("SELECT * FROM users WHERE user_id = ?", (uid,))

    def test_register_new_user(self):
        self.assertTrue(self.u.register(10, "Олексій", "З", "ФБ-56", "oleks").ok)
        r = self.row(10)
        self.assertEqual((r["first_name"], r["last_name"], r["group_name"], r["username"]),
                         ("Олексій", "З", "ФБ-56", "oleks"))
        self.assertEqual(r["free_clicks"], 5)

    def test_reregister_keeps_paid_vip_and_clicks(self):
        self.u.register(10, "А", "Б", "ФБ-56", None)
        self.core.db.execute("UPDATE users SET is_vip=1, has_paid=1, free_clicks=99 WHERE user_id=10")
        self.assertTrue(self.u.register(10, "Нове", "Імʼя", "ФБ-55", None).ok)
        r = self.row(10)
        self.assertEqual((r["is_vip"], r["has_paid"], r["free_clicks"]), (1, 1, 99))
        self.assertEqual((r["first_name"], r["group_name"]), ("Нове", "ФБ-55"))

    def test_duplicate_name_other_user_rejected(self):
        self.u.register(10, "Іван", "Іванов", "ФБ-56", None)
        res = self.u.register(11, "Іван", "Іванов", "ФБ-55", None)
        self.assertEqual(res.status, RegisterStatus.DUPLICATE_NAME)
        self.assertIsNone(self.row(11))

    def test_same_user_same_name_is_not_duplicate(self):
        self.u.register(10, "Іван", "Іванов", "ФБ-56", None)
        self.assertTrue(self.u.register(10, "Іван", "Іванов", "ФБ-55", None).ok)

    def test_username_kept_when_new_is_none(self):
        self.u.register(10, "А", "Б", "ФБ-56", "nick")
        self.u.register(10, "А", "Б", "ФБ-56", None)
        self.assertEqual(self.row(10)["username"], "nick")

    def test_touch_username_only_fills_empty(self):
        self.u.register(10, "А", "Б", "ФБ-56", None)
        self.u.touch_username(10, "first")
        self.u.touch_username(10, "second")
        self.assertEqual(self.row(10)["username"], "first")

    def test_profile_cache_invalidated(self):
        self.assertIsNone(asyncio.run(self.core.profiles.get(10)))
        asyncio.run(self.u.aregister(10, "А", "Б", "ФБ-56", None))
        self.assertEqual(asyncio.run(self.core.profiles.get(10)).group_name, "ФБ-56")


if __name__ == "__main__":
    unittest.main()
