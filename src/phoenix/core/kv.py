"""Сховище «ключ-значення» поверх старої таблиці `bot_settings` + генератори ключів.

Усі формати ключів — в одному місці (більше жодних помилок у f-рядках на 20 000 рядків).
"""
from __future__ import annotations

from .db import Database


class Keys:
    """Генератори ключів. Формат ТОЧНО як у старому боті (сумісність із його даними)."""

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
        # Атомарний upsert (старий код робив DELETE + INSERT — між ними ключ зникав)
        self._db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def set_if_absent(self, key: str, value: object) -> bool:
        """Записати, лише якщо ключа ще нема. True — записали; False — вже був."""
        cur = self._db.execute(
            "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(value))
        )
        return cur.rowcount == 1

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))

    def with_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """Усі ключі з префіксом. Діапазон по первинному ключу (швидше за LIKE)."""
        if not prefix:
            raise ValueError("prefix не може бути порожнім")
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        rows = self._db.query_all(
            "SELECT key, value FROM bot_settings WHERE key >= ? AND key < ? ORDER BY key",
            (prefix, upper),
        )
        return [(r["key"], r["value"]) for r in rows]
