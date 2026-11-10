"""Тести потоку реєстрації: імʼя -> прізвище -> група (кнопка або «Інша»), помилки, /restart."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from phoenix.core.bootstrap import build_core
from phoenix.features import registration as reg
from phoenix.features import start
from phoenix.tg.router import CallbackRouter
from phoenix.tg.text_router import TextRouter

from test_core import make_settings


def run(coro):
    return asyncio.run(coro)


class Flow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        self.ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"core": self.core}), user_data={})
        self.router, self.texts = CallbackRouter(), TextRouter()
        self.app = SimpleNamespace(add_handler=lambda *a, **k: None)
        reg.register(self.app, self.router, self.texts)

    def msg_update(self, text=None, uid=42, chat="private"):
        msg = SimpleNamespace(text=text, reply_text=AsyncMock())
        upd = SimpleNamespace(effective_user=SimpleNamespace(id=uid, first_name="Тест", username="tester"),
                              effective_chat=SimpleNamespace(type=chat), effective_message=msg,
                              callback_query=None)
        return upd, msg

    def cb_update(self, data, uid=42, chat="private"):
        msg = SimpleNamespace(reply_text=AsyncMock())
        q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=msg)
        upd = SimpleNamespace(effective_user=SimpleNamespace(id=uid, first_name="Тест", username="tester"),
                              effective_chat=SimpleNamespace(type=chat), effective_message=msg, callback_query=q)
        return upd, q, msg

    def say(self, text, uid=42):
        upd, msg = self.msg_update(text, uid)
        run(self.texts.dispatch(upd, self.ctx))
        return msg

    def press(self, data, uid=42):
        upd, q, msg = self.cb_update(data, uid)
        run(self.router.dispatch(upd, self.ctx))
        return q, msg

    def begin(self, uid=42):
        upd, msg = self.msg_update(uid=uid)
        run(start.cmd_start(upd, self.ctx))
        return msg

    def row(self, uid=42):
        return self.core.db.query_one("SELECT * FROM users WHERE user_id=?", (uid,))

    def reach_group_step(self, first="Анна", last="Коваль"):
        """/start, імʼя і прізвище: дійшли до вибору групи."""
        self.begin()
        self.say(first)
        self.say(last)

    def reach_custom_group_step(self):
        self.reach_group_step()
        self.press("REG_GROUP:Інша")

    # ---------- основний потік ----------
    def test_full_flow_with_group_button(self):
        self.begin()
        self.say("Олексій")
        self.assertEqual(self.ctx.user_data["await"], "reg_last")
        m = self.say("Знаковський")
        self.assertIn("групу", m.reply_text.await_args.args[0])
        q, msg = self.press("REG_GROUP:ФБ-56")
        r = self.row()
        self.assertEqual((r["first_name"], r["last_name"], r["group_name"], r["username"]),
                         ("Олексій", "Знаковський", "ФБ-56", "tester"))
        texts = [c.args[0] for c in msg.reply_text.await_args_list]
        self.assertTrue(any("Реєстрація успішна" in t for t in texts))
        self.assertNotIn("await", self.ctx.user_data)
        self.assertNotIn("reg_first_name", self.ctx.user_data)
        self.assertEqual(self.core.db.query_one("SELECT action FROM audit_logs ORDER BY id DESC")[0], "register")

    def test_full_flow_with_custom_group(self):
        self.reach_custom_group_step()
        self.assertEqual(self.ctx.user_data["await"], "reg_group_custom")
        self.say("фі-51")
        self.assertEqual(self.row()["group_name"], "ФІ-51")

    # ---------- захист від поганого вводу ----------
    def test_bad_name_is_rejected_and_state_kept(self):
        self.begin()
        m = self.say("<b>hack</b>")
        self.assertIn("лише літери", m.reply_text.await_args.args[0])
        self.assertEqual(self.ctx.user_data["await"], "reg_name")
        self.assertIsNone(self.row())

    def test_bad_custom_group_rejected(self):
        self.reach_custom_group_step()
        m = self.say("ФБ-56; DROP TABLE users")
        self.assertIn("Назва групи", m.reply_text.await_args.args[0])
        self.assertIsNone(self.row())

    def test_forged_group_button_rejected(self):
        self.reach_group_step()
        q, msg = self.press("REG_GROUP:<script>")
        q.answer.assert_any_await("Невідома група", show_alert=True)
        self.assertIsNone(self.row())

    def test_text_while_choosing_group_gives_hint(self):
        self.reach_group_step()
        reply = self.say("щось інше")
        self.assertIn("кнопкою", reply.reply_text.await_args.args[0])
        self.assertIsNone(self.row())

    def test_stale_group_button_does_not_register_anyone(self):
        q, msg = self.press("REG_GROUP:ФБ-56")      # user_data порожній (бота перезапускали)
        self.assertIn("/start", msg.reply_text.await_args.args[0])
        self.assertIsNone(self.row())

    def test_duplicate_name_restarts_name_step(self):
        self.core.users.register(7, "Іван", "Іванов", "ФБ-56", None)
        self.reach_group_step("Іван", "Іванов")
        q, msg = self.press("REG_GROUP:ФБ-55")
        self.assertIn("вже зареєстрований", msg.reply_text.await_args.args[0])
        self.assertEqual(self.ctx.user_data["await"], "reg_name")
        self.assertIsNone(self.row())

    # ---------- /restart ----------
    def test_restart_keeps_paid_and_vip(self):
        self.core.users.register(42, "Старе", "Імʼя", "ФБ-56", None)
        self.core.db.execute("UPDATE users SET has_paid=1, is_vip=1 WHERE user_id=42")
        handlers = []
        app = SimpleNamespace(add_handler=lambda h, *a, **k: handlers.append(h))
        reg.register(app, CallbackRouter(), TextRouter())
        cmd = handlers[0].callback
        upd, msg = self.msg_update()
        run(cmd(upd, self.ctx))
        self.assertEqual(self.ctx.user_data["await"], "reg_name")
        self.say("Нове")
        self.say("Прізвище")
        self.press("REG_GROUP:ФБ-55")
        r = self.row()
        self.assertEqual((r["first_name"], r["has_paid"], r["is_vip"]), ("Нове", 1, 1))

    # ---------- межі ----------
    def test_text_without_state_is_ignored(self):
        m = self.say("привіт")
        m.reply_text.assert_not_called()

    def test_group_chat_button_ignored(self):
        upd, q, msg = self.cb_update("REG_GROUP:ФБ-56", chat="supergroup")
        run(self.router.dispatch(upd, self.ctx))
        self.assertIsNone(self.row())


if __name__ == "__main__":
    unittest.main()
