"""Вигляд головного меню без Telegram. Підписи кнопок і їхні callback_data.

Підписи і callback_data взято зі старого Botsort.py (словник system_buttons). Завдяки цьому
старі й нові кнопки поводяться однаково, коли розділи переїжджають у новий бот.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple


class Btn(NamedTuple):
    label: str
    data: str


SETTINGS_BTN = Btn("⚙️ Налаштування і оплата", "SETTINGS_MAIN")


def catalog(*, folder_id: str, fav_count: int = 0, theme: str = "DEFAULT") -> dict[str, Btn]:
    """Усі кнопки кореня. folder_id це короткий код поточної папки (md5 її шляху, як у старому боті)."""
    fav = f" ({fav_count})" if fav_count else ""
    pip_boy = theme == "PIP_BOY"
    cat = {
        "org_menu": Btn("🏢 Організація", "SHOW_ORG_MAIN"),
        "btn_upload": Btn("📥 Завантажити", f"PREPARE_UPLOAD:{folder_id}"),
        "btn_folder": Btn("➕ Папка", f"PREPARE_FOLDER:{folder_id}"),
        "btn_link": Btn("🔗 Посилання", f"PREPARE_LINK:{folder_id}"),
        "btn_text": Btn("📝 Текст", f"PREPARE_TEXT:{folder_id}"),
        "btn_move": Btn("📦 Переміщення", "START_MOVE_MODE"),
        "wiki": Btn("📚 Архів знань", "KNOWLEDGE_MAIN"),
        "search": Btn("🔍 Пошук файлів", f"PREPARE_SEARCH:{folder_id}"),
        "popular": Btn("🔥 Популярні файли", "SHOW_POPULAR"),
        "fav": Btn(f"⭐ Важливе{fav}", "FAV_LIST:0"),
        "cal": Btn("📅 Календар", "CAL_SHOW_CURRENT"),
        "tools": Btn("🛠 Інструменти", "SHOW_TOOLS"),
        "hidden": Btn("🕵️ Приховані об'єкти", "SHOW_HIDDEN_MENU"),
        "music": Btn("🎵 Музика", "OPEN_MUSIC_LIB"),
        "animations": Btn("🎬 Анімації", "OPEN_ANIMATIONS"),
        "theme": Btn("⚪ Вимкнути Pip-Boy" if pip_boy else "🟢 Увімкнути Pip-Boy", "TOGGLE_THEME"),
        "polls": Btn("📊 Опитування", "START_POLL_WIZARD"),
        "archive": Btn("📂 Архів", "SHOW_ARCHIVE"),
        "btn_tag": Btn("🏷 Хештег", f"SHOW_TAG_PATH:{folder_id}"),
    }
    if pip_boy:
        cat["bg_menu"] = Btn("🎨 Фони", "SHOW_BG_MENU")
    return cat


def available_codes(cat: dict[str, Btn], tier: int, move_mode: bool = False) -> set[str]:
    """Які кнопки взагалі існують зараз. Хештег лише для Р3 і не в режимі переміщення."""
    codes = set(cat)
    if not (tier >= 3 and not move_mode):
        codes.discard("btn_tag")
    return codes


def build_rows(layout: Iterable[Iterable[str]], cat: dict[str, Btn]) -> list[list[Btn]]:
    """Рядки кнопок за розкладкою, і внизу завжди «Налаштування і оплата»."""
    rows = [[cat[code] for code in row if code in cat] for row in layout]
    rows = [row for row in rows if row]
    rows.append([SETTINGS_BTN])
    return rows


def callback_prefix(data: str) -> str:
    """«PREPARE_SEARCH:ab12» стає «PREPARE_SEARCH:», а «SHOW_POPULAR» лишається як є."""
    head, sep, _ = data.partition(":")
    return head + sep


def all_prefixes() -> list[str]:
    """Префікси всіх кнопок меню. Потрібні, щоб поставити заглушки на ще не перенесені розділи."""
    items = list(catalog(folder_id="x", theme="PIP_BOY").values()) + [SETTINGS_BTN]
    return sorted({callback_prefix(b.data) for b in items})
