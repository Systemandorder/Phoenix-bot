"""Тести сервісу файлів: безпека шляхів, права, сумісність uid зі старим ботом."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phoenix.core.access import AccessService, path_uid
from phoenix.core.db import Database
from phoenix.core.files import FileService
from phoenix.core.migrations import migrate

LEGACY = "/home/ubuntu/my_project/tasks"


class FilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        tmp = Path(self._t.name)
        self.root = tmp / "tasks"
        (self.root / "Лаби").mkdir(parents=True)
        (self.root / "Лаби" / "lab1.pdf").write_text("x")
        (self.root / "Музика").mkdir()
        (self.root / ".safe_files").mkdir()
        (self.root / "Таємна").mkdir()
        (self.root / "readme.txt").write_text("x")
        self.db = Database(tmp / "t.db")
        migrate(self.db, backup_dir=tmp / "bk")
        self.addCleanup(self.db.close)
        self.acl = AccessService(self.db, admin_id=1)
        self.fs = FileService(self.db, self.acl, self.root, legacy_root=LEGACY)

    def names(self, folder, group="G", uid=10):
        return [e.name for e in self.fs.list_dir(folder, group, uid)]

    def test_uid_matches_legacy_server_path(self):
        self.assertEqual(self.fs.uid_of(self.root / "Лаби"), path_uid(f"{LEGACY}/Лаби"))
        self.assertEqual(self.fs.uid_of(self.root), path_uid(LEGACY))

    def test_root_hides_dot_and_special_folders(self):
        self.assertEqual(self.names(self.root), ["readme.txt", "Лаби", "Таємна"])

    def test_acl_filters_listing(self):
        self.acl.set_file_groups(f"{LEGACY}/Таємна", ["ФБ-55"])
        self.assertNotIn("Таємна", self.names(self.root, "ФБ-56"))
        self.assertIn("Таємна", self.names(self.root, "ФБ-55"))
        self.assertIn("Таємна", self.names(self.root, "X", uid=1))      # адмін бачить усе

    def test_resolve_roundtrip_and_unknown(self):
        uid = self.fs.remember(self.root / "Лаби" / "lab1.pdf")
        self.assertEqual(self.fs.resolve(uid), (self.root / "Лаби" / "lab1.pdf").resolve())
        self.assertIsNone(self.fs.resolve("deadbeef"))

    def test_traversal_and_foreign_paths_rejected(self):
        self.assertIsNone(self.fs.legacy_path(self.root / ".." / "t.db"))
        self.assertEqual(self.fs.list_dir(self.root / "..", "G", 10), [])
        self.db.execute("INSERT INTO server_paths (uid, full_path) VALUES ('evil', '/etc/passwd')")
        self.db.execute("INSERT INTO server_paths (uid, full_path) VALUES ('evil2', ?)", (LEGACY + "/../../etc/passwd",))
        self.assertIsNone(self.fs.resolve("evil"))
        self.assertIsNone(self.fs.resolve("evil2"))

    def test_symlink_escape_rejected(self):
        outside = Path(self._t.name) / "outside"
        outside.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink не дозволені на цій системі")
        self.assertNotIn("link", self.names(self.root))
        self.assertIsNone(self.fs.legacy_path(link))

    def test_deleted_file_not_resolved(self):
        f = self.root / "gone.txt"
        f.write_text("x")
        uid = self.fs.remember(f)
        f.unlink()
        self.assertIsNone(self.fs.resolve(uid))

    def test_parent(self):
        self.assertIsNone(self.fs.parent(self.root))
        self.assertEqual(self.fs.parent(self.root / "Лаби"), self.root.resolve())

    def test_clicks_count_and_keep_on_remember(self):
        f = self.root / "readme.txt"
        uid = self.fs.remember(f)
        self.fs.register_click(10, uid)
        self.fs.register_click(10, uid)
        self.fs.remember(f)                                   # повторний показ папки не обнуляє лічильник
        self.assertEqual(self.db.query_one("SELECT click_count c FROM server_paths WHERE uid=?", (uid,))["c"], 2)
        self.assertEqual(self.db.query_one("SELECT clicks c FROM user_clicks WHERE user_id=10 AND file_uid=?", (uid,))["c"], 2)

    def test_personal_hide_respected(self):
        uid = self.fs.uid_of(self.root / "readme.txt")
        self.db.execute("INSERT INTO bot_settings (key, value) VALUES (?, '1')", (f"HIDE_{uid}_10",))
        self.assertNotIn("readme.txt", self.names(self.root))


if __name__ == "__main__":
    unittest.main()