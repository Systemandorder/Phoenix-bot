"""Тести екранів і кнопок штабу адміністратора (без мережі)."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from phoenix.core.admin import AuditRow, GroupInfo, UserRow
from phoenix.core.bootstrap import build_core
from phoenix.features import admin as adm_feature
from phoenix.features import admin_views as v
from phoenix.tg.router import CallbackRouter
from phoenix.tg.text_router import TextRouter

from test_core import make_settings

ADMIN = 1
NOW = datetime(2026, 11, 9, 12, 0)


def run(coro):
    return asyncio.run(coro)


def urow(uid=10, first="Анна", last="Коваль", group="ФБ-56", vip=0, paid=0, end=None, username="anna", clicks=5):
    return UserRow(uid, first, last, username, group, vip, paid, end, clicks)


def all_callbacks(rows):
    return [d for row in rows for _, d in row]


class ViewTests(unittest.TestCase):
    def test_callback_data_fits_telegram_limit(self):
        long_group = "Ц" * 12
        screens = [v.home_view(1, 1, 1), v.groups_view([GroupInfo(long_group, 5, 1, 1)]),
                   v.group_extra(long_group), v.group_grant_confirm(long_group, 12)[1],
                   v.card_view(urow(vip=1, group=long_group), 3, "причина", NOW, False)[1],
                   v.confirm_view("grant", urow(), 12)[1]]
        for rows in screens:
            for data in all_callbacks(rows if isinstance(rows, list) else rows[1]):
                self.assertLessEqual(len(data.encode()), 64, data)

    def test_card_shows_why_and_subscription(self):
        text, rows = v.card_view(urow(paid=1, end="2026-12-01 00:00:00"), 2, "активна підписка", NOW, False)
        self.assertIn("Студент (Р2)", text)
        self.assertIn("Чому: активна підписка", text)
        self.assertIn("до 01.12.2026 (ще 21 дн.)", text)
        self.assertIn("ADM:ASK:promote:10:0", all_callbacks(rows))

    def test_card_for_curator_offers_demote(self):
        _, rows = v.card_view(urow(vip=1), 3, "куратор", NOW, False)
        cbs = all_callbacks(rows)
        self.assertIn("ADM:ASK:demote:10:0", cbs)
        self.assertNotIn("ADM:ASK:promote:10:0", cbs)

    def test_card_for_admin_has_no_actions(self):
        _, rows = v.card_view(urow(), 4, "адмін", NOW, True)
        self.assertFalse([c for c in all_callbacks(rows) if c.startswith(("ADM:ASK", "ADM:EDIT"))])

    def test_expired_subscription_text(self):
        self.assertIn("завершилась", v.subscription_text(urow(paid=1, end="2026-01-01 00:00:00"), NOW))
        self.assertEqual(v.subscription_text(urow(), NOW), "немає")

    def test_html_in_names_is_escaped_everywhere(self):
        evil = urow(first="<b>x</b>", last="<script>", username="<i>")
        self.assertNotIn("<script>", v.card_view(evil, 1, "<b>", NOW, False)[0])
        self.assertNotIn("<script>", v.curators_view({"<g>": [evil]})[0])
        self.assertNotIn("<b>x", v.confirm_text("promote", evil, 1))
        log = v.log_view([AuditRow(1, 1, "<b>", "<script>", "2026-11-09 01:02:03")], 1, 0)[0]
        self.assertNotIn("<script>", log)

    def test_list_pagination_buttons(self):
        people = [(urow(uid=i), 1) for i in range(8)]
        _, rows = v.list_view("T", people, 20, 1, lambda p: f"ADM:USR:{p}", "ADM:HOME")
        cbs = all_callbacks(rows)
        self.assertIn("ADM:USR:0", cbs)
        self.assertIn("ADM:USR:2", cbs)

    def test_empty_curators_explains_how_to_add(self):
        self.assertIn("Зробити куратором", v.curators_view({})[0])

    def test_group_without_name_has_no_bulk_grant(self):
        self.assertEqual(v.group_extra("—"), [])


class FlowBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_core(make_settings(Path(self._tmp.name)))
        self.addCleanup(self.core.close)
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"core": self.core}),
                                   user_data={}, bot=self.bot)
        self.router, self.texts = CallbackRouter(), TextRouter()
        self.handlers = []
        app = SimpleNamespace(add_handler=lambda h, *a, **k: self.handlers.append(h))
        adm_feature.register(app, self.router, self.texts)
        db = self.core.db
        for uid, f, l, g in [(10, "Анна", "Коваль", "ФБ-56"), (11, "Іван", "Іванов", "ФБ-55"),
                             (12, "Петро", "Сидоренко", "ФБ-55")]:
            db.execute("INSERT INTO users (user_id, first_name, last_name, group_name) VALUES (?,?,?,?)",
                       (uid, f, l, g))

    def update(self, data=None, text=None, uid=ADMIN, chat="private"):
        msg = SimpleNamespace(text=text, reply_text=AsyncMock(), delete=AsyncMock())
        q = None
        if data is not None:
            q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=msg)
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid), effective_chat=SimpleNamespace(type=chat),
                               effective_message=msg, callback_query=q)

    def press(self, data, uid=ADMIN, chat="private"):
        upd = self.update(data=data, uid=uid, chat=chat)
        run(self.router.dispatch(upd, self.ctx))
        return upd.callback_query

    def say(self, text, uid=ADMIN):
        upd = self.update(text=text, uid=uid)
        run(self.texts.dispatch(upd, self.ctx))
        return upd.effective_message

    def shown(self, q):
        return q.edit_message_text.await_args.args[0]

    def user(self, uid):
        return self.core.db.query_one("SELECT * FROM users WHERE user_id=?", (uid,))


class AccessGuardTests(FlowBase):
    def test_non_admin_button_is_refused_and_changes_nothing(self):
        q = self.press("ADM:DO:promote:10:0", uid=99)
        q.answer.assert_awaited_once_with("⛔ Лише для адміністратора", show_alert=True)
        self.assertEqual(self.user(10)["is_vip"], 0)

    def test_admin_button_in_group_chat_refused(self):
        q = self.press("ADM:HOME", chat="supergroup")
        q.answer.assert_awaited_once_with("⛔ Лише для адміністратора", show_alert=True)

    def test_command_ignored_for_strangers(self):
        cmd = next(h for h in self.handlers if getattr(h, "commands", None) == frozenset({"admin"}))
        upd = self.update(uid=99)
        run(cmd.callback(upd, self.ctx))
        upd.effective_message.reply_text.assert_not_called()

    def test_command_opens_home_for_admin(self):
        cmd = next(h for h in self.handlers if getattr(h, "commands", None) == frozenset({"admin"}))
        upd = self.update()
        run(cmd.callback(upd, self.ctx))
        self.assertIn("Штаб адміністратора", upd.effective_message.reply_text.await_args.args[0])

    def test_non_admin_cannot_use_admin_text_states(self):
        self.ctx.user_data.update({"await": "adm_group", "adm_target": 10})
        self.say("ФІ-51", uid=99)
        self.assertEqual(self.user(10)["group_name"], "ФБ-56")
        self.assertNotIn("await", self.ctx.user_data)


class NavigationTests(FlowBase):
    def test_home_shows_counts(self):
        q = self.press("ADM:HOME")
        self.assertIn("Користувачів: <b>3</b>", self.shown(q))

    def test_users_list_groups_and_curators_screens(self):
        self.assertIn("Усі користувачі", self.shown(self.press("ADM:USR:0")))
        q = self.press("ADM:GRPS")
        labels = [b.text for row in q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any(t.startswith("ФБ-55 · 2 ос.") for t in labels), labels)
        self.assertIn("Група ФБ-55", self.shown(self.press("ADM:GRP:ФБ-55:0")))
        self.assertIn("Кураторів поки немає", self.shown(self.press("ADM:CUR")))

    def test_card_back_returns_to_origin_list(self):
        self.press("ADM:GRP:ФБ-55:0")
        self.press("ADM:U:11")
        q = self.press("ADM:BACK")
        self.assertIn("Група ФБ-55", self.shown(q))

    def test_broken_button_does_not_crash(self):
        q = self.press("ADM:USR:abc")
        q.answer.assert_any_await("Кнопка застаріла. Відкрийте /admin", show_alert=True)

    def test_close_deletes_message(self):
        upd = self.update(data="ADM:CLOSE")
        run(self.router.dispatch(upd, self.ctx))
        upd.effective_message.delete.assert_awaited_once()


class ActionTests(FlowBase):
    def test_promote_needs_confirmation_then_changes_and_notifies(self):
        q = self.press("ADM:ASK:promote:10:0")
        self.assertIn("куратором", self.shown(q))
        self.assertEqual(self.user(10)["is_vip"], 0)                # ще нічого не змінилось
        q = self.press("ADM:DO:promote:10:0")
        self.assertEqual(self.user(10)["is_vip"], 1)
        self.assertIn("куратор", q.answer.await_args.args[0])
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], 10)
        self.assertIn("🎓", self.shown(q))                          # картка оновилась
        self.assertIn("Куратор (Р3)", self.shown(q))

    def test_cancelled_confirmation_changes_nothing(self):
        self.press("ADM:ASK:expire:10:0")
        self.press("ADM:U:10")
        self.assertEqual(self.user(10)["has_paid"], 0)

    def test_grant_months_and_audit(self):
        self.press("ADM:DO:grant:11:3")
        r = self.user(11)
        self.assertEqual((r["has_paid"], r["free_clicks"]), (1, 20))
        last = self.core.db.query_one("SELECT action FROM audit_logs ORDER BY id DESC")
        self.assertEqual(last["action"], "СХВАЛЕННЯ_ОПЛАТИ")

    def test_error_is_shown_as_alert(self):
        q = self.press("ADM:DO:demote:10:0")                         # не куратор
        self.assertIn("не куратор", q.answer.await_args.args[0])

    def test_bulk_group_grant_flow(self):
        q = self.press("ADM:GG:ФБ-55:3")
        self.assertIn("усім студентам групи", self.shown(q))
        self.assertEqual(self.user(11)["has_paid"], 0)
        q = self.press("ADM:GGDO:ФБ-55:3")
        self.assertEqual((self.user(11)["has_paid"], self.user(12)["has_paid"], self.user(10)["has_paid"]), (1, 1, 0))
        self.assertIn("2 осіб", q.answer.await_args.args[0])


class TextInputTests(FlowBase):
    def test_find_flow(self):
        self.press("ADM:FIND")
        self.assertEqual(self.ctx.user_data["await"], "adm_find")
        msg = self.say("СИДОРЕНКО")
        self.assertIn("Результат пошуку", msg.reply_text.await_args.args[0])
        self.assertNotIn("await", self.ctx.user_data)

    def test_change_group_flow_with_retry_on_bad_input(self):
        self.press("ADM:EDITG:10")
        msg = self.say("ФБ; DROP")
        self.assertIn("Спробуйте ще раз", msg.reply_text.await_args.args[0])
        self.assertEqual(self.ctx.user_data["await"], "adm_group")     # лишились у стані
        msg = self.say("фі-51")
        self.assertEqual(self.user(10)["group_name"], "ФІ-51")
        self.assertIn("ФБ-56 -> ФІ-51", msg.reply_text.await_args.args[0])

    def test_clicks_flow(self):
        self.press("ADM:EDITC:10")
        self.say("abc")
        self.assertEqual(self.ctx.user_data["await"], "adm_clicks")
        self.say("42")
        self.assertEqual(self.user(10)["free_clicks"], 42)

    def test_lost_target_is_handled(self):
        self.ctx.user_data["await"] = "adm_clicks"
        msg = self.say("5")
        self.assertIn("загубились", msg.reply_text.await_args.args[0])

    def test_log_screen(self):
        self.press("ADM:DO:grant:11:1")
        q = self.press("ADM:LOG:0")
        self.assertIn("СХВАЛЕННЯ_ОПЛАТИ", self.shown(q))


if __name__ == "__main__":
    unittest.main()