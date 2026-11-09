"""Штаб адміністратора: керування людьми, кураторами, групами і підписками прямо з бота.

Відкривається командою /admin (тільки головний адміністратор, тільки в особистому чаті).
Усі кнопки мають префікс ADM:. Перша перевірка в обробнику: хто натиснув. Підроблена кнопка від
не-адміна нічого не змінить. Небезпечні дії питають підтвердження («Так / Скасувати»).
Кожна зміна пише рядок у журнал дій.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from ..core.admin import AdminError
from ..tg.helpers import core_of, edit_or_send
from ..tg.router import CallbackRouter
from ..tg.text_router import TextRouter, clear, expect
from . import admin_views as v

log = logging.getLogger(__name__)

S_FIND, S_GROUP, S_CLICKS = "adm_find", "adm_group", "adm_clicks"
K_TARGET, K_BACK = "adm_target", "adm_back"


def markup(rows: v.Rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


async def _io(fn, *args):
    """Запити до бази виконуємо поза циклом подій, щоб бот не «підвисав»."""
    return await asyncio.to_thread(fn, *args)


def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return (user is not None and chat is not None and chat.type == ChatType.PRIVATE
            and user.id == core_of(context).settings.admin_id)


async def _screen(update: Update, text: str, rows: v.Rows) -> None:
    await edit_or_send(update, text, markup(rows))


# ---------------- екрани ----------------
async def show_home(update, context) -> None:
    adm = core_of(context).admin
    n, g, c = await _io(adm.counts)
    await _screen(update, *v.home_view(n, g, c))


async def show_users(update, context, group: str | None, page: int) -> None:
    adm = core_of(context).admin
    rows_, total = await _io(adm.list_users, group, page * v.PER_PAGE, v.PER_PAGE)
    people = [(u, int(adm.tier_of(u)[0])) for u in rows_]
    if group is None:
        cb, back, title, extra = (lambda p: f"ADM:USR:{p}"), "ADM:HOME", "👥 <b>Усі користувачі</b>", None
        context.user_data[K_BACK] = f"ADM:USR:{page}"
    else:
        cb, back = (lambda p: f"ADM:GRP:{group}:{p}"), "ADM:GRPS"
        title, extra = f"📁 <b>Група {v.esc(group)}</b>", v.group_extra(group)
        context.user_data[K_BACK] = f"ADM:GRP:{group}:{page}"
    await _screen(update, *v.list_view(title, people, total, page, cb, back, extra))


async def show_card(update, context, user_id: int) -> None:
    core = core_of(context)
    u = await _io(core.admin.get_user, user_id)
    if u is None:
        await _screen(update, "❌ Користувача не знайдено.", [[("⬅️ Штаб", "ADM:HOME")]])
        return
    tier, reason = core.admin.tier_of(u)
    text, rows = v.card_view(u, int(tier), reason, datetime.now(), user_id == core.settings.admin_id)
    await _screen(update, text, rows)


async def _notify(context, user_id: int, text: str) -> None:
    """Повідомити людину про зміну. Якщо вона заблокувала бота, це не помилка."""
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.info("Не вдалося повідомити %s: %s", user_id, e)


async def do_action(update, context, verb: str, uid: int, months: int) -> None:
    core, actor = core_of(context), update.effective_user.id
    try:
        if verb == "promote":
            msg = await _io(core.admin.promote, actor, uid)
            await _notify(context, uid, "🎓 Вам надано права <b>куратора</b> (рівень 3). /start")
        elif verb == "demote":
            msg = await _io(core.admin.demote, actor, uid)
        elif verb == "grant":
            msg = await _io(core.admin.grant_member, actor, uid, months)
            await _notify(context, uid, f"🎉 Вам активовано <b>Оператор (Рівень 2)</b> на {months} міс.!")
        elif verb == "expire":
            msg = await _io(core.admin.expire, actor, uid)
        else:
            return
    except AdminError as e:
        await update.callback_query.answer(str(e), show_alert=True)
        await show_card(update, context, uid)
        return
    await update.callback_query.answer(msg, show_alert=True)
    await show_card(update, context, uid)


# ---------------- обробник усіх кнопок ADM: ----------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update, context):
        await query.answer("⛔ Лише для адміністратора", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 2 or parts[1] not in ("DO", "GGDO"):    # ці дві самі відповідають спливаючим вікном
        await query.answer()
    await _route(update, context, query.data)


async def _route(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    parts = data.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    core = core_of(context)
    try:
        if cmd == "HOME":
            clear(context)
            await show_home(update, context)
        elif cmd == "NOOP":
            return
        elif cmd == "CLOSE":
            clear(context)
            await query.message.delete()
        elif cmd == "USR":
            await show_users(update, context, None, int(parts[2]))
        elif cmd == "GRPS":
            await _screen(update, *v.groups_view(await _io(core.admin.list_groups)))
        elif cmd == "GRP":
            group, page = ":".join(parts[2:-1]), int(parts[-1])
            await show_users(update, context, group, page)
        elif cmd == "CUR":
            await _screen(update, *v.curators_view(await _io(core.admin.curators)))
        elif cmd == "U":
            await show_card(update, context, int(parts[2]))
        elif cmd == "BACK":
            await _route(update, context, context.user_data.get(K_BACK) or "ADM:HOME")  # туди, звідки відкрили картку
        elif cmd == "ASK":
            verb, uid, months = parts[2], int(parts[3]), int(parts[4])
            u = await _io(core.admin.get_user, uid)
            if u is None:
                await _screen(update, "❌ Користувача не знайдено.", [[("⬅️ Штаб", "ADM:HOME")]])
            else:
                await _screen(update, *v.confirm_view(verb, u, months))
        elif cmd == "DO":
            await do_action(update, context, parts[2], int(parts[3]), int(parts[4]))
        elif cmd == "GG":
            group, months = ":".join(parts[2:-1]), int(parts[-1])
            await _screen(update, *v.group_grant_confirm(group, months))
        elif cmd == "GGDO":
            group, months = ":".join(parts[2:-1]), int(parts[-1])
            try:
                msg, _ = await _io(core.admin.grant_group, update.effective_user.id, group, months)
            except AdminError as e:
                msg = str(e)
            await query.answer(msg, show_alert=True)
            await show_users(update, context, group, 0)
        elif cmd == "FIND":
            expect(context, S_FIND)
            await _screen(update, "🔎 <b>Пошук людини</b>\nНапишіть прізвище, імʼя, @username або ID:",
                          [[("⬅️ Штаб", "ADM:HOME")]])
        elif cmd == "EDITG":
            context.user_data[K_TARGET] = int(parts[2])
            expect(context, S_GROUP)
            await _screen(update, "🔁 Напишіть нову групу (наприклад ФБ-56):", [[("❌ Скасувати", f"ADM:U:{parts[2]}")]])
        elif cmd == "EDITC":
            context.user_data[K_TARGET] = int(parts[2])
            expect(context, S_CLICKS)
            await _screen(update, "🧮 Напишіть, скільки безкоштовних кліків має бути (0-100000):",
                          [[("❌ Скасувати", f"ADM:U:{parts[2]}")]])
        elif cmd == "LOG":
            page = int(parts[2])
            entries, total = await _io(core.admin.recent_log, page * v.LOG_PER_PAGE, v.LOG_PER_PAGE)
            await _screen(update, *v.log_view(entries, total, page))
        else:
            log.warning("Невідома адмін-кнопка: %r", data[:64])
    except (ValueError, IndexError):
        log.warning("Пошкоджена адмін-кнопка: %r", data[:64])
        with suppress(TelegramError):
            await query.answer("Кнопка застаріла. Відкрийте /admin", show_alert=True)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update, context):
        return                                   # як у старому боті: чужим не відповідаємо
    clear(context)
    await show_home(update, context)


# ---------------- введення тексту ----------------
def register(app: Application, router: CallbackRouter, texts: TextRouter) -> None:
    router.add("ADM:", on_button, answer=False)
    app.add_handler(CommandHandler("admin", cmd_admin))

    @texts.on(S_FIND)
    async def on_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_admin(update, context):
            clear(context)
            return
        core = core_of(context)
        found = await _io(core.admin.search, update.effective_message.text)
        clear(context)
        people = [(u, int(core.admin.tier_of(u)[0])) for u in found]
        context.user_data[K_BACK] = "ADM:HOME"
        text, rows = v.list_view("🔎 <b>Результат пошуку</b>", people, len(people), 0,
                                 lambda p: "ADM:HOME", "ADM:HOME")
        await update.effective_message.reply_text(text, reply_markup=markup(rows), parse_mode=ParseMode.HTML)

    @texts.on(S_GROUP)
    async def on_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _apply_text(update, context, "set_group", lambda t: t)

    @texts.on(S_CLICKS)
    async def on_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        def to_int(t: str) -> int:
            if not t.strip().lstrip("-").isdigit():
                raise AdminError("⚠️ Потрібне ціле число.")
            return int(t.strip())
        await _apply_text(update, context, "set_clicks", to_int)


async def _apply_text(update, context, method: str, parse) -> None:
    if not _is_admin(update, context):
        clear(context)
        return
    core = core_of(context)
    target = context.user_data.get(K_TARGET)
    if target is None:
        clear(context)
        await update.effective_message.reply_text("⚠️ Дані загубились. Відкрийте людину ще раз: /admin")
        return
    try:
        msg = await _io(getattr(core.admin, method), update.effective_user.id, target,
                        parse(update.effective_message.text))
    except AdminError as e:
        await update.effective_message.reply_text(f"{e}\nСпробуйте ще раз або /admin")
        return                                   # лишаємося в тому ж стані: можна виправити ввід
    clear(context)
    context.user_data.pop(K_TARGET, None)
    await update.effective_message.reply_text(msg, reply_markup=markup([[("👤 До картки", f"ADM:U:{target}")]]))