"""Тести ядра Phoenix. Запуск з кореня репозиторію командою  pytest  (або  python -m unittest)."""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from phoenix.config import ConfigError, Settings
from phoenix.core.access import Tier, path_uid
from phoenix.core.bootstrap import build_core
from phoenix.core.crypto import KeyVaultError
from phoenix.core.db import Database
from phoenix.core.kv import KV, Keys
from phoenix.core.migrations import MIGRATIONS, migrate
from phoenix.logging_setup import RedactingFormatter, redact

LATEST = MIGRATIONS[-1][0]
FAKE_TOKEN = "bot123456789:" + "A" * 35


def make_settings(tmp: Path, **over) -> Settings:
    base = dict(bot_token="x", admin_id=1, master_key=Fernet.generate_key().decode(), data_dir=tmp,
                db_path=tmp / "t.db", timezone="Europe/Kyiv", home_group="G", vip_ids=frozenset({5}),
                wrap_user_keys=False, log_level="INFO")
    base.update(over)
    return Settings(**base)


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class ConfigTests(TmpCase):
    ENV = {"BOT_TOKEN": "t", "ADMIN_ID": "7", "DATA_DIR": ""}

    def env(self, **extra):
        env = dict(self.ENV, DATA_DIR=str(self.tmp), MASTER_KEY=Fernet.generate_key().decode())
        env.update(extra)  # значення з тесту мають перекривати типові
        return mock.patch.dict(os.environ, env, clear=True)

    def test_reads_values(self):
        with self.env():
            s = Settings.from_env()
        self.assertEqual(s.admin_id, 7)
        self.assertEqual(s.timezone, "Europe/Kyiv")

    def test_missing_token_gives_clear_error(self):
        with self.env():
            del os.environ["BOT_TOKEN"]
            with self.assertRaises(ConfigError):
                Settings.from_env()

    def test_bad_master_key_rejected(self):
        with self.env(MASTER_KEY="не-ключ"):
            with self.assertRaises(ConfigError):
                Settings.from_env()

    def test_secrets_not_in_repr(self):
        with self.env(BOT_TOKEN="СУПЕР_СЕКРЕТ"):
            s = Settings.from_env()
        self.assertNotIn("СУПЕР_СЕКРЕТ", repr(s))
        self.assertNotIn(s.master_key, repr(s))


class DatabaseTests(TmpCase):
    def test_transaction_commit_and_rollback(self):
        db = Database(self.tmp / "a.db")
        db.execute("CREATE TABLE t (x INTEGER)")
        with db.tx() as c:
            c.execute("INSERT INTO t VALUES (1)")
        with self.assertRaises(RuntimeError):
            with db.tx() as c:
                c.execute("INSERT INTO t VALUES (2)")
                raise RuntimeError("збій посередині")
        self.assertEqual([r["x"] for r in db.query_all("SELECT x FROM t")], [1])
        db.close()

    def test_reconnects_after_close(self):
        db = Database(self.tmp / "a.db")
        self.assertEqual(db.query_one("SELECT 1")[0], 1)
        db.close()
        self.assertEqual(db.query_one("SELECT 2")[0], 2)
        db.close()

    def test_works_from_several_threads(self):
        db = Database(self.tmp / "a.db")
        db.execute("CREATE TABLE t (x INTEGER)")
        errors = []

        def work(n):
            try:
                for i in range(20):
                    db.execute("INSERT INTO t VALUES (?)", (n * 100 + i,))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(db.query_one("SELECT count(*) FROM t")[0], 80)
        db.close()

    def test_backup_is_readable(self):
        db = Database(self.tmp / "a.db")
        db.execute("CREATE TABLE t (x INTEGER)")
        db.execute("INSERT INTO t VALUES (42)")
        db.backup_to(self.tmp / "copy.db")
        con = sqlite3.connect(self.tmp / "copy.db")
        self.assertEqual(con.execute("SELECT x FROM t").fetchone()[0], 42)
        con.close()
        db.close()


class MigrationTests(TmpCase):
    def test_fresh_database_reaches_latest(self):
        db = Database(self.tmp / "n.db")
        self.assertEqual(migrate(db, self.tmp / "bk"), LATEST)
        self.assertEqual(migrate(db, self.tmp / "bk"), LATEST)  # повторний запуск нічого не змінює
        db.close()

    def test_existing_data_survives_and_backup_is_made(self):
        con = sqlite3.connect(self.tmp / "o.db")
        con.execute("CREATE TABLE org_data (id INTEGER PRIMARY KEY, group_name TEXT, parent_id INTEGER, category TEXT)")
        con.execute("INSERT INTO org_data VALUES (1, 'G', 0, 'c')")
        con.commit()
        con.close()
        db = Database(self.tmp / "o.db")
        migrate(db, self.tmp / "bk")
        self.assertEqual(db.query_one("SELECT count(*) FROM org_data")[0], 1)
        self.assertTrue(list((self.tmp / "bk").glob("*.db")))
        db.close()

    def test_refuses_database_from_newer_version(self):
        db = Database(self.tmp / "n.db")
        db.execute(f"PRAGMA user_version = {LATEST + 1}")
        with self.assertRaises(RuntimeError):
            migrate(db)
        db.close()


class KVTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.db = Database(self.tmp / "k.db")
        migrate(self.db)
        self.kv = KV(self.db)
        self.addCleanup(self.db.close)

    def test_set_get_delete(self):
        self.kv.set("A", 5)
        self.assertEqual(self.kv.get("A"), "5")
        self.kv.set("A", "6")
        self.assertEqual(self.kv.get("A"), "6")
        self.kv.delete("A")
        self.assertIsNone(self.kv.get("A"))
        self.assertFalse(self.kv.exists("A"))

    def test_set_if_absent(self):
        self.assertTrue(self.kv.set_if_absent("B", "1"))
        self.assertFalse(self.kv.set_if_absent("B", "2"))
        self.assertEqual(self.kv.get("B"), "1")

    def test_prefix_search(self):
        for k in ("P_1", "P_2", "Q_1"):
            self.kv.set(k, "x")
        self.assertEqual([k for k, _ in self.kv.with_prefix("P_")], ["P_1", "P_2"])
        with self.assertRaises(ValueError):
            self.kv.with_prefix("")

    def test_key_formats_match_legacy(self):
        self.assertEqual(Keys.hide("ab12", 7), "HIDE_ab12_7")
        self.assertEqual(Keys.deleted("ab12", "G"), "DEL_ab12_G")
        self.assertEqual(Keys.grant("ab12", 7), "GRANT_ab12_7")
        self.assertEqual(Keys.crypto(7), "CRYPTO_KEY_7")


class CryptoTests(TmpCase):
    def test_roundtrip(self):
        core = build_core(make_settings(self.tmp))
        self.addCleanup(core.close)
        token = core.vault.encrypt(3, "привіт".encode())
        self.assertEqual(core.vault.decrypt(3, token).decode(), "привіт")

    def test_user_cannot_read_other_users_data(self):
        core = build_core(make_settings(self.tmp))
        self.addCleanup(core.close)
        token = core.vault.encrypt(3, b"secret")
        with self.assertRaises(KeyVaultError):
            core.vault.decrypt(4, token)

    def test_text_instead_of_bytes_is_rejected(self):
        core = build_core(make_settings(self.tmp))
        self.addCleanup(core.close)
        with self.assertRaises(TypeError):
            core.vault.encrypt(3, "рядок")  # type: ignore[arg-type]

    def test_wrapped_keys_are_not_stored_in_plain_text(self):
        core = build_core(make_settings(self.tmp, wrap_user_keys=True))
        self.addCleanup(core.close)
        core.vault.encrypt(3, b"x")
        self.assertTrue(core.kv.get(Keys.crypto(3)).startswith("enc1:"))

    def test_changed_master_key_gives_clear_error(self):
        first = build_core(make_settings(self.tmp, wrap_user_keys=True))
        token = first.vault.encrypt(3, b"x")
        first.close()
        second = build_core(make_settings(self.tmp, wrap_user_keys=True))  # інший MASTER_KEY
        self.addCleanup(second.close)
        with self.assertRaises(KeyVaultError):
            second.vault.decrypt(3, token)


class AccessTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.core = build_core(make_settings(self.tmp))
        self.addCleanup(self.core.close)
        self.acl = self.core.access

    def test_admin_tier_and_simulation(self):
        self.assertEqual(self.acl.get_tier(1, None), Tier.ADMIN)
        self.assertEqual(self.acl.get_tier(1, None, mock_tier=2), Tier.MEMBER)
        self.assertEqual(self.acl.get_tier(9, None, mock_tier=4), Tier.GUEST)  # симуляція лише для адміна

    def test_stranger_is_guest(self):
        self.assertEqual(self.acl.get_tier(9, None), Tier.GUEST)

    def test_admin_sees_everything(self):
        self.assertTrue(self.acl.can_access_file("tasks/x.pdf", None, 1))

    def test_private_file_only_for_owner(self):
        path = "tasks/y.pdf"
        self.acl.set_owner(path_uid(path), "PRIVATE_7", path)
        self.assertTrue(self.acl.can_access_file(path, "G", 7))
        self.assertFalse(self.acl.can_access_file(path, "G", 9))

    def test_path_traversal_is_blocked(self):
        self.assertFalse(self.acl.can_access_file("tasks/../x", None, 1))
        self.assertFalse(self.acl.can_access_file("tasks\\..\\x", None, 1))

    def test_error_means_deny(self):
        with mock.patch.object(self.acl, "_decide", side_effect=RuntimeError("збій")):                                                                            
            self.assertFalse(self.acl.can_access_file("tasks/x.pdf", "G", 9))


class AuditTests(TmpCase):
    def test_writes_row(self):
        core = build_core(make_settings(self.tmp))
        self.addCleanup(core.close)
        core.audit.log(1, "open_file", "tasks/x.pdf")
        row = core.db.query_one("SELECT user_id, action, target FROM audit_logs ORDER BY id DESC LIMIT 1")
        self.assertEqual(tuple(row), (1, "open_file", "tasks/x.pdf"))

    def test_failure_never_raises(self):
        core = build_core(make_settings(self.tmp))
        core.db.execute("DROP TABLE audit_logs")
        self.addCleanup(core.close)
        core.audit.log(1, "x", "y")  # не повинно кинути помилку


class LoggingTests(unittest.TestCase):
    def test_token_is_hidden_in_text(self):
        self.assertNotIn("A" * 35, redact(f"GET https://api.telegram.org/{FAKE_TOKEN}/getMe"))

    def test_password_pairs_are_hidden(self):
        self.assertNotIn("qwerty", redact("login password=qwerty ok"))

    def test_token_is_hidden_in_traceback(self):
        fmt = RedactingFormatter("%(message)s")
        try:
            raise RuntimeError(f"помилка {FAKE_TOKEN}")
        except RuntimeError:
            import sys
            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "збій", (), sys.exc_info())
        self.assertNotIn("A" * 35, fmt.format(rec))


if __name__ == "__main__":
    unittest.main()
