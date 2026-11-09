"""Тести пояснень доступу до файлів і керування правами (група, людина)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phoenix.core.access import path_uid
from phoenix.core.bootstrap import build_core

from test_core import make_settings

F = "tasks/lab1.pdf"


class AclTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        self.acl = self.core.access

    def why(self, group, uid, path=F):
        return self.acl.explain_file(path, group, uid)

    def test_explanations_for_each_rule(self):
        self.acl.set_file_groups(F, ["ФБ-55", "ФБ-56"])
        d = self.why("ФБ-56", 10)
        self.assertEqual((d.allowed, d.reason), (True, "доступ для групи ФБ-56"))
        d = self.why("ФІ-51", 10)
        self.assertFalse(d.allowed)
        self.assertIn("ФБ-55, ФБ-56", d.reason)
        self.assertTrue(self.why("ФІ-51", 1).allowed)          # адмін
        self.assertIn("адміністратор", self.why("ФІ-51", 1).reason)
        self.assertIn("обмежень немає", self.why("X", 10, "tasks/other.pdf").reason)
        self.assertIn("публічна", self.why("X", 10, "Архів знань/a.pdf").reason)

    def test_personal_grant_opens_group_file_for_one_person(self):
        self.acl.set_file_groups(F, ["ФБ-55"])
        self.assertFalse(self.why("ФІ-51", 10).allowed)
        self.acl.grant_person(F, 10)
        d = self.why("ФІ-51", 10)
        self.assertTrue(d.allowed)
        self.assertIn("особистий дозвіл", d.reason)
        self.assertFalse(self.why("ФІ-51", 11).allowed)         # іншим по-старому закрито
        self.acl.revoke_person(F, 10)
        self.assertFalse(self.why("ФІ-51", 10).allowed)

    def test_private_file_owner_and_grant(self):
        self.acl.set_file_groups(F, ["PRIVATE_10"])
        self.assertIn("власник", self.why("X", 10).reason)
        self.assertFalse(self.why("X", 11).allowed)
        self.acl.grant_person(F, 11)
        self.assertTrue(self.why("X", 11).allowed)

    def test_remove_for_group_beats_group_access(self):
        self.acl.set_file_groups(F, ["ALL"])
        self.acl.remove_for_group(F, "ФБ-55")
        d = self.why("ФБ-55", 10)
        self.assertFalse(d.allowed)
        self.assertIn("прибрано для групи ФБ-55", d.reason)
        self.assertTrue(self.why("ФБ-56", 10).allowed)

    def test_describe_file_summary(self):
        self.acl.set_file_groups(F, ["ФБ-55", "ФБ-56"])
        self.acl.grant_person(F, 10)
        self.acl.grant_person(F, 11)
        self.acl.remove_for_group(F, "ФІ-51")
        s = self.acl.describe_file(F)
        self.assertEqual((s.restriction, s.groups), ("groups", ("ФБ-55", "ФБ-56")))
        self.assertEqual(s.granted, (10, 11))
        self.assertEqual(s.removed_for_groups, ("ФІ-51",))
        self.assertEqual(s.label, "🔒 Лише групи: ФБ-55, ФБ-56")
        self.assertEqual(s.file_uid, path_uid(F))

    def test_describe_file_other_kinds(self):
        self.assertEqual(self.acl.describe_file(F).restriction, "none")
        self.acl.set_file_groups(F, ["ALL"])
        self.assertEqual(self.acl.describe_file(F).restriction, "all")
        self.acl.set_file_groups(F, ["PRIVATE_7"])
        s = self.acl.describe_file(F)
        self.assertEqual((s.restriction, s.private_owner), ("private", 7))
        self.acl.set_file_groups(F, None)
        self.assertEqual(self.acl.describe_file(F).restriction, "none")

    def test_explain_matches_can_access_everywhere(self):
        self.acl.set_file_groups(F, ["ФБ-55"])
        self.acl.grant_person(F, 12)
        for group, uid in [("ФБ-55", 10), ("ФІ-51", 10), ("ФІ-51", 12), ("ФІ-51", 1), ("", 0)]:
            self.assertEqual(self.acl.can_access_file(F, group, uid), self.why(group, uid).allowed)

    def test_traversal_denied_with_reason(self):
        d = self.acl.explain_file("tasks/../secret", "G", 1)
        self.assertEqual((d.allowed, d.reason), (False, "некоректний шлях"))

    def test_tier_reason_for_admin(self):
        tier, why = self.acl.explain_tier(1, None)
        self.assertEqual(int(tier), 4)
        self.assertIn("адміністратор", why)


if __name__ == "__main__":
    unittest.main()