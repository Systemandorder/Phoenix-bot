"""Тести підписів і callback_data меню. Значення взято зі старого Botsort.py (system_buttons)."""
from __future__ import annotations

import unittest

from phoenix.core.menu import AUTO_CODES
from phoenix.features import menu_view as mv

OLD = {  # код, підпис, callback_data у старому боті, для folder_id = "ab12cd34"
    "org_menu": ("🏢 Організація", "SHOW_ORG_MAIN"),
    "btn_upload": ("📥 Завантажити", "PREPARE_UPLOAD:ab12cd34"),
    "btn_folder": ("➕ Папка", "PREPARE_FOLDER:ab12cd34"),
    "btn_link": ("🔗 Посилання", "PREPARE_LINK:ab12cd34"),
    "btn_text": ("📝 Текст", "PREPARE_TEXT:ab12cd34"),
    "btn_move": ("📦 Переміщення", "START_MOVE_MODE"),
    "wiki": ("📚 Архів знань", "KNOWLEDGE_MAIN"),
    "search": ("🔍 Пошук файлів", "PREPARE_SEARCH:ab12cd34"),
    "popular": ("🔥 Популярні файли", "SHOW_POPULAR"),
    "fav": ("⭐ Важливе", "FAV_LIST:0"),
    "cal": ("📅 Календар", "CAL_SHOW_CURRENT"),
    "tools": ("🛠 Інструменти", "SHOW_TOOLS"),
    "hidden": ("🕵️ Приховані об'єкти", "SHOW_HIDDEN_MENU"),
    "music": ("🎵 Музика", "OPEN_MUSIC_LIB"),
    "animations": ("🎬 Анімації", "OPEN_ANIMATIONS"),
    "theme": ("🟢 Увімкнути Pip-Boy", "TOGGLE_THEME"),
    "polls": ("📊 Опитування", "START_POLL_WIZARD"),
    "archive": ("📂 Архів", "SHOW_ARCHIVE"),
    "btn_tag": ("🏷 Хештег", "SHOW_TAG_PATH:ab12cd34"),
}


class CatalogTests(unittest.TestCase):
    def test_labels_and_callbacks_match_old_bot(self):
        cat = mv.catalog(folder_id="ab12cd34")
        for code, (label, data) in OLD.items():
            with self.subTest(code=code):
                self.assertEqual((cat[code].label, cat[code].data), (label, data))

    def test_every_code_is_known_to_the_menu_model(self):
        self.assertLessEqual(set(mv.catalog(folder_id="x", theme="PIP_BOY")), set(AUTO_CODES))

    def test_backgrounds_button_only_in_pip_boy_theme(self):
        self.assertNotIn("bg_menu", mv.catalog(folder_id="x"))
        pip = mv.catalog(folder_id="x", theme="PIP_BOY")
        self.assertIn("bg_menu", pip)
        self.assertEqual(pip["theme"].label, "⚪ Вимкнути Pip-Boy")

    def test_favourites_counter(self):
        self.assertEqual(mv.catalog(folder_id="x", fav_count=3)["fav"].label, "⭐ Важливе (3)")

    def test_tag_button_rule(self):
        cat = mv.catalog(folder_id="x")
        self.assertNotIn("btn_tag", mv.available_codes(cat, 2))
        self.assertIn("btn_tag", mv.available_codes(cat, 3))
        self.assertNotIn("btn_tag", mv.available_codes(cat, 3, move_mode=True))


class RowsTests(unittest.TestCase):
    def test_settings_button_is_always_last(self):
        rows = mv.build_rows([["cal"]], mv.catalog(folder_id="x"))
        self.assertEqual(rows[-1], [mv.SETTINGS_BTN])

    def test_unknown_codes_are_skipped_and_empty_rows_dropped(self):
        rows = mv.build_rows([["nope"], ["cal", "nope"]], mv.catalog(folder_id="x"))
        self.assertEqual([[b.data for b in r] for r in rows], [["CAL_SHOW_CURRENT"], ["SETTINGS_MAIN"]])


class PrefixTests(unittest.TestCase):
    def test_prefix_extraction(self):
        self.assertEqual(mv.callback_prefix("PREPARE_SEARCH:ab12"), "PREPARE_SEARCH:")
        self.assertEqual(mv.callback_prefix("SHOW_POPULAR"), "SHOW_POPULAR")

    def test_all_prefixes_cover_every_button(self):
        prefixes = mv.all_prefixes()
        for btn in list(mv.catalog(folder_id="x", theme="PIP_BOY").values()) + [mv.SETTINGS_BTN]:
            self.assertTrue(any(btn.data.startswith(p) for p in prefixes), btn.data)
        self.assertEqual(len(prefixes), len(set(prefixes)))


if __name__ == "__main__":
    unittest.main()
