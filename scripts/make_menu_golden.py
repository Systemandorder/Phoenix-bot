"""Знімає «золотий» знімок меню зі справжнього старого ui_auto.py.

Використання з кореня репозиторію
    python scripts/make_menu_golden.py legacy/ui_auto.py

Результат tests/fixtures/menu_golden.json. Тести порівнюють нову модель меню з ним.
Старий файл лише читається, його код виконується тільки в цьому скрипті.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

EXTRA_CODES = ["file_gear", "org_btn_move", "org_btn_add", "org_btn_folder", "org_btn_media",
               "wiki_add", "wiki_graph", "util"]
ROLES = [(False, 1), (False, 2), (False, 3), (True, 4)]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not Path(argv[1]).is_file():
        print(__doc__)
        return 2
    spec = importlib.util.spec_from_file_location("old_ui_auto", argv[1])
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)

    codes = list(old.AUTO_CODES) + EXTRA_CODES
    defaults = [{"code": c, "admin": a, "tier": t, "enabled": bool(old.default_enabled(c, a, t))}
                for c in codes for a, t in ROLES]

    rnd = random.Random(2026)
    base = set(old.AUTO_CODES)
    subsets = [base] + [base - {c} for c in old.AUTO_CODES] + [{c} for c in old.AUTO_CODES] + [set()]
    subsets += [{c for c in old.AUTO_CODES if rnd.random() < 0.5} for _ in range(300)]
    packs = [{"visible": sorted(s), "rows": [list(r) for r in old.pack(s, 4)]} for s in subsets]

    out = Path("tests/fixtures/menu_golden.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"auto_codes": list(old.AUTO_CODES), "defaults": defaults, "packs": packs},
                              ensure_ascii=False), encoding="utf-8")
    print(f"Збережено {out}: {len(defaults)} правил за замовчуванням, {len(packs)} розкладок")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
