"""Key-value store on top of the legacy `bot_settings` table + typed key builders."""
from __future__ import annotations

from .db import Database


class Keys:
    """All key formats in one place (no more f-string typos across 20k lines)."""

    @staticmethod
    def hide(uid: str, user_id: int) -> str:
        return f"HIDE_{uid}_{user_id}"

    @staticmethod
    def deleted(uid: str, group: str) -> str:
        return f"DEL_{uid}_{group}"

    @staticmethod
    def grant(uid: str, user_id: int) -> str:
        return f"GRANT_{uid}_{user_id}"

    @staticmethod
    def simulation(user_id: int) -> str:
        return f"SIMULATION_MODE_{user_id}"

    @staticmethod
    def crypto(user_id: int) -> str:
        return f"CRYPTO_KEY_{user_id}"


class KV:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.query_one("SELECT value FROM bot_settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def exists(self, key: str) -> bool:
        return self._db.query_one("SELECT 1 FROM bot_settings WHERE key = ?", (key,)) is not None

    def set(self, key: str, value: object) -> None:
        # atomic upsert (legacy did DELETE + INSERT)
        self._db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def set_if_absent(self, key: str, value: object) -> bool:
        cur = self._db.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(value)))
        return cur.rowcount == 1

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))

    def with_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """Range scan on the primary key (faster than LIKE)."""
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        rows = self._db.query_all(
            "SELECT key, value FROM bot_settings WHERE key >= ? AND key < ? ORDER BY key", (prefix, upper)
        )
        return [(r["key"], r["value"]) for r in rows]
