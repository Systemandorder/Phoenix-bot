"""Тести моделі меню. Головне, що вони доводять. Меню збігається зі старим ботом.

Файл tests/fixtures/menu_golden.json це знімок, знятий зі справжнього старого ui_auto.py.
Якщо хтось змінить правила меню, тест покаже, де нова поведінка розходиться зі старою.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phoenix.core.db import Database
from phoenix.core.kv import KV
from phoenix.core.menu import (AUTO_CODES, MenuService, Scope, default_enabled, key_all, key_group,
                               key_person, pack, visible_codes)
from phoenix.core.migrations import migrate

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "menu_golden.json").read_text(encoding="utf-8"))


class GoldenParityTests(unittest.TestCase):
    """Нова модель повинна давати рівно те, що давав старий код."""

    def test_code_list_and_order_are_the_same(self):
        self.assertEqual(AUTO_CODES, GOLDEN["auto_codes"])

    def test_default_enabled_matches_old(self):
        for case in GOLDEN["defaults"]:
            with self.subTest(**case):
                self.assertEqual(default_enabled(case["code"], case["admin"], case["tier"]), case["enabled"])

    def test_pack_matches_old(self):
        for case in GOLDEN["packs"]:
            with self.subTest(visible=case["visible"]):
                self.assertEqual(pack(case["visible"]), case["rows"])


class PackShapeTests(unittest.TestCase):
    def test_fav_is_alone_on_top(self):
        self.assertEqual(pack({"fav", "cal", "org_menu"})[0], ["fav"])

    def test_no_button_lost_or_duplicated(self):
        vis = set(AUTO_CODES)
        flat = [c for row in pack(vis) for c in row]
        self.assertEqual(sorted(flat), sorted(vis))

    def test_rows_have_at_most_two_buttons(self):
        self.assertTrue(all(len(r) <= 2 for r in pack(set(AUTO_CODES))))

    def test_empty(self):
        self.assertEqual(pack(set()), [])


class VisibilityTests(unittest.TestCase):
    ALL = set(AUTO_CODES)

    def vis(self, tier, enabled=lambda c: True, move=False):
        return visible_codes(self.ALL, enabled, tier, move)

    def test_guest_never_sees_management_buttons(self):
        got = self.vis(1)
        for code in ("tools", "hidden", "polls", "btn_upload", "btn_folder", "btn_link", "btn_text", "btn_move"):
            self.assertNotIn(code, got)

    def test_tier2_gets_tools_but_not_upload(self):
        got = self.vis(2)
        self.assertIn("tools", got)
        self.assertNotIn("btn_upload", got)

    def test_tier3_gets_everything_when_enabled(self):
        self.assertEqual(self.vis(3), self.ALL)

    def test_move_button_hidden_in_move_mode(self):
        self.assertNotIn("btn_move", self.vis(3, move=True))

    def test_disabled_button_is_hidden(self):
        self.assertNotIn("cal", self.vis(3, enabled=lambda c: c != "cal"))

    def test_unavailable_button_is_hidden(self):
        self.assertNotIn("bg_menu", visible_codes(self.ALL - {"bg_menu"}, lambda c: True, 4))


class MenuServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database(Path(self._tmp.name) / "m.db")
        migrate(self.db)
        self.addCleanup(self.db.close)
        self.svc = MenuService(KV(self.db))

    def on(self, code="cal", user=7, group="ФБ-56", admin=False, tier=2):
        return self.svc.is_enabled(code, user, group, admin, tier)

    def test_defaults_apply_when_nothing_is_set(self):
        self.assertTrue(self.on("cal"))
        self.assertFalse(self.on("music"))

    def test_person_key_has_old_bot_format(self):
        self.assertEqual(key_person("cal", 7), "UI_cal_7")
        self.db.execute("INSERT INTO bot_settings (key, value) VALUES ('UI_cal_7', '0')")
        self.assertFalse(self.on("cal", user=7))   # вибір зі старого бота працює

    def test_group_setting_applies_to_the_group_only(self):
        self.svc.set_enabled(Scope(group="ФБ-56"), "music", True)
        self.assertTrue(self.on("music", group="ФБ-56"))
        self.assertFalse(self.on("music", group="ФБ-55"))

    def test_global_setting_applies_to_everyone(self):
        self.svc.set_enabled(Scope(), "cal", False)
        self.assertFalse(self.on("cal", group="ФБ-55"))

    def test_person_beats_group_beats_all(self):
        self.svc.set_enabled(Scope(), "theme", True)
        self.svc.set_enabled(Scope(group="ФБ-56"), "theme", False)
        self.assertFalse(self.on("theme", user=7, group="ФБ-56"))
        self.svc.set_enabled(Scope(user_id=7), "theme", True)
        self.assertTrue(self.on("theme", user=7, group="ФБ-56"))
        self.assertTrue(self.on("theme", user=8, group="ФБ-55"))     # лише глобальне

    def test_clear_returns_to_default(self):
        self.svc.set_enabled(Scope(user_id=7), "cal", False)
        self.svc.clear(Scope(user_id=7), "cal")
        self.assertTrue(self.on("cal"))

    def test_new_keys_do_not_collide_with_old_ones(self):
        self.assertEqual(key_group("cal", "ФБ-56"), "UIG_cal_ФБ-56")
        self.assertEqual(key_all("cal"), "UIA_cal")
        self.assertNotEqual(key_group("cal", "7"), key_person("cal", 7))

    def test_gear_is_on_for_tier2_like_old_bot(self):
        self.assertTrue(self.on("file_gear", tier=2))
        self.assertFalse(self.on("file_gear", tier=1))

    def test_layout_for_guest_and_moderator(self):
        avail = set(AUTO_CODES)
        guest = self.svc.layout(avail, 7, "X", False, 1)
        mod = self.svc.layout(avail, 8, "X", False, 3)
        self.assertEqual(guest[0], ["fav"])
        self.assertNotIn("btn_upload", [c for r in guest for c in r])
        self.assertIn("btn_upload", [c for r in mod for c in r])

    def test_group_can_hide_a_button_for_a_whole_group(self):
        self.svc.set_enabled(Scope(group="ФБ-55"), "popular", False)
        rows = self.svc.layout(set(AUTO_CODES), 9, "ФБ-55", False, 2)
        self.assertNotIn("popular", [c for r in rows for c in r])


if __name__ == "__main__":
    unittest.main()
