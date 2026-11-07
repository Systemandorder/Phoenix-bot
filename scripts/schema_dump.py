"""Точна схема БД як SQL (CREATE TABLE / INDEX). БЕЗ самих даних.

Використання: python scripts/schema_dump.py legacy/botsort_copy.db legacy/schema.sql
Службові таблиці повнотекстового пошуку (FTS5: *_config, *_content, *_data,
*_docsize, *_idx) пропускаються: SQLite створює їх сам разом із віртуальною таблицею.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_SHADOW_SUFFIXES = ("_config", "_content", "_data", "_docsize", "_idx")


def dump_schema(src: str) -> str:
    con = sqlite3.connect(Path(src).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name").fetchall()
    finally:
        con.close()
    virtual = {n for t, n, sql in rows if t == "table" and sql.upper().startswith("CREATE VIRTUAL")}
    out = []
    for kind, name, sql in rows:
        if kind == "table" and any(name == v + s for v in virtual for s in _SHADOW_SUFFIXES):
            continue
        out.append(sql.strip().rstrip(";") + ";")
    return "\n\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or not Path(argv[1]).is_file():
        print("Використання: python scripts/schema_dump.py шлях/до/botsort.db вихід.sql")
        return 2
    text = dump_schema(argv[1])
    Path(argv[2]).write_text(text, encoding="utf-8")
    print(f"Збережено: {argv[2]} ({text.count('CREATE')} об'єктів)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
