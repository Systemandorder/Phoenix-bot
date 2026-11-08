"""Пробний запуск: міграції на КОПІЇ старої БД + перевірка, що дані не втрачено.

Використання: python scripts/check_db.py шлях/до/botsort.db
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

from phoenix.core.db import Database
from phoenix.core.migrations import migrate


def table_counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def columns(path: Path, table: str) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    finally:
        con.close()


def main(src: str) -> int:
    if not Path(src).is_file():
        print(f"ПОМИЛКА: файл не знайдено: {Path(src).resolve()}")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "copy.db"
        # as_uri() правильно екранує шлях (на Windows "legacy\x.db" інакше ламає URI)
        source = sqlite3.connect(Path(src).resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(work)
        source.backup(target)  # узгоджена копія, включно з вмістом WAL
        source.close()
        target.close()

        before = table_counts(work)
        db = Database(work)
        version = migrate(db, backup_dir=Path(tmp) / "backups")
        db.close()
        after = table_counts(work)

        bad = {t: (n, after.get(t)) for t, n in before.items() if after.get(t) != n}
        print(f"schema version: {version}, tables before: {len(before)}, after: {len(after)}")
        print("new tables:", sorted(set(after) - set(before)) or "none")
        fp = columns(work, "file_permissions")
        print("file_permissions columns:", fp)
        if "group_name" not in fp:
            print("WARNING: no group_name column (legacy code writes group_owner?)")
        if bad:
            print("DATA LOSS DETECTED:", bad)
            return 1

        # Зовнішні ключі: нове ядро вмикає їх, старий бот — ні. Сироти в даних = майбутні помилки.
        chk = sqlite3.connect(work)
        orphans = chk.execute("PRAGMA foreign_key_check").fetchall()
        chk.close()
        print(f"foreign_key_check: {len(orphans)} рядків-сиріт" + (f" {orphans[:5]}" if orphans else ""))

        # Чи відтворює нова БД «з нуля» ті самі таблиці, що й жива?
        fresh_path = Path(tmp) / "fresh.db"
        fresh = Database(fresh_path)
        migrate(fresh, backup_dir=None)
        fresh.close()
        missing = sorted(set(table_counts(work)) - set(table_counts(fresh_path)))
        print("відсутні в новій БД з нуля:", missing or "немає (схема повна)")
        if missing:
            return 1
        print("OK: row counts identical in all pre-existing tables")
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/check_db.py /path/to/botsort.db")
    sys.exit(main(sys.argv[1]))
    sys.exit(main(sys.argv[1]))
