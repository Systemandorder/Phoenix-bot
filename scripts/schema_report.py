"""Звіт про схему БД: таблиці, стовпці, кількість рядків. БЕЗ самих даних.

Використання: python scripts/schema_report.py legacy/botsort_copy.db [вихідний_файл.txt]
Безпечно показувати: токенів, ключів і вмісту рядків у звіті немає.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def build_report(src: str) -> str:
    uri = Path(src).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        lines = [f"Таблиць: {len(tables)}", ""]
        for t in tables:
            cols = [f"{r[1]} {r[2]}".strip() + (" PK" if r[5] else "")
                    for r in con.execute(f'PRAGMA table_info("{t}")')]
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            lines.append(f"{t} ({n} рядків): " + ", ".join(cols))
        idx = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        lines += ["", f"Індекси ({len(idx)}): " + (", ".join(idx) or "немає")]
        return "\n".join(lines)
    finally:
        con.close()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or not Path(argv[1]).is_file():
        print("Використання: python scripts/schema_report.py шлях/до/botsort.db [звіт.txt]")
        return 2
    report = build_report(argv[1])
    if len(argv) == 3:
        Path(argv[2]).write_text(report, encoding="utf-8")
        print(f"Звіт збережено: {argv[2]}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
