"""Список можливостей бота: команди, кнопки, слухачі, фонові задачі, таблиці бази.

Два режими.

1. Зняти список зі старого бота
   python scripts/feature_inventory.py legacy/Botsort.py legacy/inventory_old.json

2. Порівняти старий список з новим ботом (що ще не перенесено)
   python scripts/feature_inventory.py --diff legacy/inventory_old.json legacy/inventory_new.json

Файл бота лише читається, нічого не запускається і не змінюється.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

_TABLE_RE = re.compile(r"(?:FROM|INTO|UPDATE|JOIN|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+([a-z_][a-z0-9_]*)", re.I)
_SQL_WORDS = {"select", "set", "values", "where", "if", "not", "exists"}


def _name(node: ast.AST) -> str:
    return node.id if isinstance(node, ast.Name) else ast.unparse(node)[:40]


def build_inventory(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    commands: set[str] = set()
    listeners: set[str] = set()
    jobs: set[str] = set()
    buttons: set[str] = set()
    tables: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            kind = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if kind == "CommandHandler" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    commands.add("/" + first.value)
            elif kind == "MessageHandler" and len(node.args) > 1:
                listeners.add(_name(node.args[1]))
            elif kind in {"run_repeating", "run_daily", "run_once", "run_monthly"} and node.args:
                jobs.add(_name(node.args[0]))
        # кнопки: data.startswith("ABC") та data == "ABC"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {"data", "query_data", "cb"}:
                for arg in node.args:
                    for const in ast.walk(arg):
                        if isinstance(const, ast.Constant) and isinstance(const.value, str):
                            buttons.add(const.value)
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id in {"data", "query_data", "cb"}:
            for comp in node.comparators:
                for const in ast.walk(comp):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str):
                        buttons.add(const.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 600:
            for match in _TABLE_RE.findall(node.value):
                if match.lower() not in _SQL_WORDS:
                    tables.add(match.lower())

    return {
        "commands": sorted(commands),
        "listeners": sorted(listeners),
        "jobs": sorted(jobs),
        "buttons": sorted(buttons),
        "tables": sorted(tables),
    }


def diff(old: dict, new: dict) -> int:
    missing_total = 0
    for section in ("commands", "listeners", "jobs", "buttons", "tables"):
        missing = sorted(set(old.get(section, [])) - set(new.get(section, [])))
        missing_total += len(missing)
        print(f"{section}: у старому {len(old.get(section, []))}, у новому {len(new.get(section, []))}, "
              f"ще не перенесено {len(missing)}")
        for item in missing[:40]:
            print("   ", item)
        if len(missing) > 40:
            print(f"    ... і ще {len(missing) - 40}")
    return 0 if missing_total == 0 else 1


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--diff":
        old = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        new = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        return diff(old, new)
    if len(argv) == 3 and Path(argv[1]).is_file():
        inv = build_inventory(Path(argv[1]))
        Path(argv[2]).write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
        for section, items in inv.items():
            print(f"{section}: {len(items)}")
        print(f"Збережено: {argv[2]}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
