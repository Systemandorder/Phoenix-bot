"""Тести шару Telegram: роутер кнопок, /start, збирання застосунку (без мережі)."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from phoenix.app import build_application
from phoenix.core.bootstrap import build_core
from phoenix.features import start
from phoenix.tg.router import STALE_TEXT, CallbackRouter, RouterError

from test_core import make_settings


def run(coro):
    return asyncio.run(coro)


def fake_update(data: str):
    query = SimpleNamespace(data=data, answer=AsyncMock())
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1)), query


class RouterTests(unittest.TestCase):
    def test_longest_prefix_wins(self):
        r = CallbackRouter()
        short, long_ = AsyncMock(), AsyncMock()
        r.add("CAL", short)
        r.add("CAL_NAV:", long_)
        self.assertIs(r.resolve("CAL_NAV:2026:11:next").handler, long_)
        self.assertIs(r.resolve("CAL_DAY:01.01.2026").handler, short)
        self.assertIsNone(r.resolve("FAV:1"))

    def test_duplicate_and_empty_prefix_rejected(self):
        r = CallbackRouter()
        r.add("A:", AsyncMock())
        with self.assertRaises(RouterError):
            r.add("A:", AsyncMock())
        with self.assertRaises(RouterError):
            r.add("", AsyncMock())

    def test_dispatch_answers_then_calls_handler(self):
        r = CallbackRouter()
        order = []
        upd, query = fake_update("X:1")
        query.answer.side_effect = lambda *a, **k: order.append("answer")

        async def h(u, c):
            order.append("handler")
        r.add("X:", h)
        run(r.dispatch(upd, MagicMock()))
        self.assertEqual(order, ["answer", "handler"])

    def test_answer_false_leaves_answer_to_handler(self):
        r = CallbackRouter()
        h = AsyncMock()
        r.add("X:", h, answer=False)
        upd, query = fake_update("X:1")
        run(r.dispatch(upd, MagicMock()))
        query.answer.assert_not_called()
        h.assert_awaited_once()

    def test_unknown_button_gets_explanation(self):
        r = CallbackRouter()
        upd, query = fake_update("OLD_BUTTON")
        run(r.dispatch(upd, MagicMock()))
        query.answer.assert_awaited_once_with(STALE_TEXT, show_alert=True)

    def test_no_callback_query_is_ignored(self):
        r = CallbackRouter()
        run(r.dispatch(SimpleNamespace(callback_query=None), MagicMock()))


class StartTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        self.ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"core": self.core}), user_data={})

    def _update(self, user_id):
        msg = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id, first_name="Тест", username=None),
                               effective_chat=SimpleNamespace(type="private"),
                               effective_message=msg), msg

    def test_unknown_user_starts_registration(self):
        upd, msg = self._update(999)
        run(start.cmd_start(upd, self.ctx))
        self.assertIn("ІМ'Я", msg.reply_text.await_args.args[0])
        self.assertEqual(self.ctx.user_data["await"], "reg_name")

    def test_start_in_group_asks_for_private_chat(self):
        upd, msg = self._update(999)
        upd.effective_chat.type = "supergroup"
        run(start.cmd_start(upd, self.ctx))
        self.assertIn("особисті", msg.reply_text.await_args.args[0])
        self.assertNotIn("await", self.ctx.user_data)

    def test_registered_user_sees_group_and_tier(self):
        self.core.db.execute("INSERT INTO users (user_id, first_name, last_name, group_name) VALUES (?,?,?,?)",
                             (42, "Олексій", "З", "G"))   # G = home_group у make_settings
        upd, msg = self._update(42)
        run(start.cmd_start(upd, self.ctx))
        text = msg.reply_text.await_args.args[0]
        self.assertIn("Олексій", text)
        self.assertIn("Група: G", text)
        self.assertIn("Р2", text)

    def test_admin_tier(self):
        self.core.db.execute("INSERT INTO users (user_id, first_name, group_name) VALUES (1, 'Адм', 'X')")
        upd, msg = self._update(1)   # admin_id=1
        run(start.cmd_start(upd, self.ctx))
        self.assertIn("Адмін", msg.reply_text.await_args.args[0])


class AppTests(unittest.TestCase):
    def test_build_application_wires_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = build_core(make_settings(Path(tmp), bot_token="123456:" + "A" * 35))
            try:
                app = build_application(core)
                handlers = [h for group in app.handlers.values() for h in group]
                names = {c for h in handlers if isinstance(h, CommandHandler) for c in h.commands}
                self.assertEqual(names, {"start", "ping", "restart"})
                self.assertEqual(sum(isinstance(h, CallbackQueryHandler) for h in handlers), 1)
                self.assertEqual(sum(isinstance(h, MessageHandler) for h in handlers), 1)
                self.assertIs(app.bot_data["core"], core)
                self.assertTrue(app.error_handlers)
            finally:
                core.close()


if __name__ == "__main__":
    unittest.main()