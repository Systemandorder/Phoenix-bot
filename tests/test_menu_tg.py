"""Тести головного меню в Telegram (без мережі). Перевіряють, що бачить людина кожного рівня
і що кнопки ще не перенесених розділів відповідають зрозуміло."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from phoenix.core.bootstrap import build_core
from phoenix.core.menu import Scope
from phoenix.features import menu as menu_feature
from phoenix.tg.router import CallbackRouter

from test_core import make_settings


def run(coro):
    return asyncio.run(coro)


class MenuCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))      # home_group = "G", admin_id = 1
        self.addCleanup(self.core.close)
        self.ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"core": self.core}), user_data={})

    def add_user(self, uid, group="X-1", vip=0, paid=0):
        self.core.db.execute("INSERT INTO users (user_id, first_name, last_name, group_name, is_vip, has_paid) "
                             "VALUES (?, 'Тест', 'Тестов', ?, ?, ?)", (uid, group, vip, paid))

    def update(self, uid):
        msg = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid, first_name="Тест"),
                               effective_message=msg), msg

    def datas(self, uid):
        upd, msg = self.update(uid)
        run(menu_feature.show_main_menu(upd, self.ctx))
        markup = msg.reply_text.await_args.kwargs["reply_markup"]
        return [b.callback_data for row in markup.inline_keyboard for b in row], markup, msg


class ShowMenuTests(MenuCase):
    def test_unregistered_person_is_sent_to_register(self):
        upd, msg = self.update(999)
        run(menu_feature.show_main_menu(upd, self.ctx))
        self.assertIn("/start", msg.reply_text.await_args.args[0])

    def test_guest_sees_basics_but_no_management(self):
        self.add_user(20)
        data, _, _ = self.datas(20)
        self.assertIn("CAL_SHOW_CURRENT", data)
        self.assertIn("SHOW_ORG_MAIN", data)
        self.assertFalse(any(d.startswith("PREPARE_UPLOAD") for d in data))
        self.assertNotIn("SHOW_TOOLS", data)

    def test_member_gets_tools(self):
        self.add_user(21, group="G")                                     # основна група, рівень 2
        data, _, _ = self.datas(21)
        self.assertIn("SHOW_TOOLS", data)
        self.assertFalse(any(d.startswith("PREPARE_UPLOAD") for d in data))

    def test_moderator_gets_upload_and_folder_buttons(self):
        self.add_user(22, vip=1)
        data, _, _ = self.datas(22)
        self.assertTrue(any(d.startswith("PREPARE_UPLOAD:") for d in data))
        self.assertTrue(any(d.startswith("PREPARE_FOLDER:") for d in data))

    def test_settings_button_is_last_and_card_is_shown(self):
        self.add_user(20)
        data, markup, msg = self.datas(20)
        self.assertEqual(data[-1], "SETTINGS_MAIN")
        self.assertIn("Рівень доступу", msg.reply_text.await_args.args[0])

    def test_group_can_hide_a_button_for_everybody_in_it(self):
        self.add_user(20, group="Z-9")
        self.core.menu.set_enabled(Scope(group="Z-9"), "popular", False)
        data, _, _ = self.datas(20)
        self.assertNotIn("SHOW_POPULAR", data)

    def test_favourites_counter_is_shown(self):
        self.add_user(20)
        self.core.db.execute("CREATE TABLE IF NOT EXISTS bkb_fav (user_id INTEGER, rel TEXT, PRIMARY KEY(user_id, rel))")
        self.core.db.execute("INSERT INTO bkb_fav (user_id, rel) VALUES (20, 'a'), (20, 'b')")
        _, markup, _ = self.datas(20)
        labels = [b.text for row in markup.inline_keyboard for b in row]
        self.assertIn("⭐ Важливе (2)", labels)

    def test_menu_survives_missing_section_tables(self):
        self.add_user(20)
        self.core.db.execute("DROP TABLE IF EXISTS bkb_fav")
        self.core.db.execute("DROP TABLE IF EXISTS bkb_favx")
        data, _, _ = self.datas(20)
        self.assertIn("CAL_SHOW_CURRENT", data)

    def test_folder_code_is_stable_md5_of_files_dir(self):
        from phoenix.core.access import path_uid
        self.add_user(22, vip=1)
        data, _, _ = self.datas(22)
        expected = path_uid(str(self.core.settings.files_dir))
        self.assertIn(f"PREPARE_UPLOAD:{expected}", data)


class RouterWiringTests(MenuCase):
    def make_router(self):
        router = CallbackRouter()
        menu_feature.register(SimpleNamespace(), router, None)
        return router

    def press(self, router, data, uid=20):
        q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
        upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=uid, first_name="Тест"),
                              effective_message=SimpleNamespace(reply_text=AsyncMock()))
        run(router.dispatch(upd, self.ctx))
        return q

    def test_unported_button_explains_instead_of_going_silent(self):
        router = self.make_router()
        q = self.press(router, "SHOW_POPULAR")
        q.answer.assert_awaited_once()
        self.assertIn("переноситься", q.answer.await_args.args[0])
        self.assertTrue(q.answer.await_args.kwargs["show_alert"])

    def test_every_menu_button_has_a_route(self):
        router = self.make_router()
        for btn in menu_feature.menu_view.catalog(folder_id="ab12", theme="PIP_BOY").values():
            self.assertIsNotNone(router.resolve(btn.data), btn.data)
        self.assertIsNotNone(router.resolve("SETTINGS_MAIN"))

    def test_ported_section_is_not_overridden_by_placeholder(self):
        router = CallbackRouter()
        real = AsyncMock()
        router.add("CAL_SHOW_CURRENT", real)
        menu_feature.register(SimpleNamespace(), router, None)
        self.add_user(20)
        self.press(router, "CAL_SHOW_CURRENT")
        real.assert_awaited_once()

    def test_home_buttons_redraw_the_menu(self):
        self.add_user(20)
        router = self.make_router()
        for data in ("ROOT_ID", "REFRESH_FOLDER", "MENU_HOME"):
            q = self.press(router, data)
            q.edit_message_text.assert_awaited()
            q.edit_message_text.reset_mock()

    def test_ignore_button_is_silent(self):
        router = self.make_router()
        q = self.press(router, "IGNORE")
        q.edit_message_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
