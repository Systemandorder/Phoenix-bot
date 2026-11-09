"""Модель головного меню: які кнопки бачить людина і в якому порядку вони стоять.

Тут немає нічого від Telegram, лише правила. Тому все перевіряється тестами без бота.
Поведінка збігається зі старим ботом (ui_auto.py і Botsort.py). Збіг перевіряють тести
на знімку, знятому зі старого коду (tests/fixtures/menu_golden.json).

Нове порівняно зі старим ботом. Налаштування кнопки можна задати на трьох рівнях.
Людина, потім група, потім усі. Перший знайдений рівень виграє, далі діє правило за замовчуванням.
Старий бот знав лише рівень людини, і його ключі працюють як раніше.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .kv import KV

# Порядок кодів як у старому ui_auto.AUTO_CODES
ALL_ORDER = ["cal", "fav", "search", "popular", "wiki", "archive", "org_menu"]
MANAGE_ORDER = ["tools", "polls", "btn_tag", "btn_upload", "btn_folder", "btn_link", "btn_text", "btn_move"]
EXTRA_ORDER = ["music", "animations", "theme", "bg_menu", "hidden"]
AUTO_CODES = ALL_ORDER + MANAGE_ORDER + EXTRA_ORDER

# Розкладка: спочатку широка «Важливе», потім пари за змістом, далі решта по одній
_BASE_PAIRS = [["cal", "org_menu"], ["search", "popular"], ["wiki", "archive"]]
_FLAT = ["tools", "hidden", "polls", "btn_upload", "btn_folder", "btn_link", "btn_text", "btn_move", "btn_tag",
         "music", "animations", "theme", "bg_menu"]

# Що вимкнено за замовчуванням
_MOD_HIDDEN = {"music", "animations", "theme", "hidden", "bg_menu", "file_gear"}      # рівень 3 і адмін
_BASIC_HIDDEN = {"btn_upload", "btn_folder", "btn_link", "btn_text", "btn_move", "music", "animations",
                 "tools", "polls", "hidden", "bg_menu", "file_gear", "theme",
                 "org_btn_move", "org_btn_add", "org_btn_folder", "org_btn_media", "wiki_add", "wiki_graph"}

# Мінімальний рівень, без якого кнопка не показується взагалі (навіть якщо її ввімкнули)
_NEEDS_TIER_2 = {"tools", "hidden"}
_NEEDS_TIER_3 = {"btn_upload", "btn_folder", "btn_link", "btn_text", "btn_move", "polls"}


def default_enabled(code: str, is_admin: bool, tier: int) -> bool:
    """Стан кнопки, коли ніхто нічого не перемикав."""
    if not is_admin and tier == 2 and code in ("tools", "hidden"):
        return True                      # Р2: Інструменти й Приховані одразу в меню
    if is_admin or tier >= 3:
        return code not in _MOD_HIDDEN
    return code not in _BASIC_HIDDEN


def pack(visible: Iterable[str]) -> list[list[str]]:
    """Авто-розташування: «Важливе» зверху, далі пари, без самотніх кнопок посередині."""
    vis = set(visible)
    rows: list[list[str]] = [["fav"]] if "fav" in vis else []
    units = [[c for c in pair if c in vis] for pair in _BASE_PAIRS] + [[c] for c in _FLAT if c in vis]
    pending: str | None = None
    for unit in units:
        if not unit:
            continue
        if len(unit) == 2:
            if pending:
                rows.append([pending])
                pending = None
            rows.append(unit)
        elif pending:
            rows.append([pending, unit[0]])
            pending = None
        else:
            pending = unit[0]
    if pending:
        rows.append([pending])
    return rows


def visible_codes(available: Iterable[str], enabled: Callable[[str], bool], tier: int,
                  move_mode: bool = False) -> set[str]:
    """Кнопки, які людина реально побачить. Ті самі умови, що в старому _ui_auto_layout."""
    avail = set(available)
    out: set[str] = set()
    for code in AUTO_CODES:
        if code not in avail or not enabled(code):
            continue
        if code in _NEEDS_TIER_2 and tier < 2:
            continue
        if code in _NEEDS_TIER_3 and tier < 3:
            continue
        if code == "btn_move" and move_mode:
            continue
        out.add(code)
    return out


# ---------------- налаштування кнопок у базі ----------------
def key_person(code: str, user_id: int) -> str:
    return f"UI_{code}_{user_id}"          # формат старого бота


def key_group(code: str, group: str) -> str:
    return f"UIG_{code}_{group}"


def key_all(code: str) -> str:
    return f"UIA_{code}"


@dataclass(frozen=True, slots=True)
class Scope:
    """Кому застосовується налаштування. Рівно одне з полів заповнене, або порожньо для «усіх»."""
    user_id: int | None = None
    group: str | None = None

    def key(self, code: str) -> str:
        if self.user_id is not None:
            return key_person(code, self.user_id)
        if self.group:
            return key_group(code, self.group)
        return key_all(code)


class MenuService:
    def __init__(self, kv: KV) -> None:
        self._kv = kv

    def is_enabled(self, code: str, user_id: int, group: str | None, is_admin: bool, tier: int) -> bool:
        """Людина, потім група, потім усі, потім правило за замовчуванням."""
        for key in (key_person(code, user_id),
                    key_group(code, group) if group else None,
                    key_all(code)):
            if key is None:
                continue
            value = self._kv.get(key)
            if value is not None:
                return value == "1"
        if code == "file_gear" and tier >= 2:
            return True                   # як у старому боті
        return default_enabled(code, is_admin, tier)

    def set_enabled(self, scope: Scope, code: str, enabled: bool) -> None:
        self._kv.set(scope.key(code), "1" if enabled else "0")

    def clear(self, scope: Scope, code: str) -> None:
        """Повернути правило за замовчуванням для цього охоплення."""
        self._kv.delete(scope.key(code))

    def layout(self, available: Iterable[str], user_id: int, group: str | None, is_admin: bool,
               tier: int, move_mode: bool = False) -> list[list[str]]:
        enabled = lambda code: self.is_enabled(code, user_id, group, is_admin, tier)  # noqa: E731
        return pack(visible_codes(available, enabled, tier, move_mode))
