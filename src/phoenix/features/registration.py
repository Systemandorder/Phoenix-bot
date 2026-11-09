"""Реєстрація: імʼя, прізвище, група. Той самий потік, що й у старому боті.

/start без профілю -> імʼя -> прізвище -> кнопки груп (або «Інша» з ручним вводом) -> запис у users.

Що покращено порівняно зі старим ботом:
* імʼя, прізвище і групу перевіряємо (старий приймав будь-що, навіть теги);
* імена екрануються перед показом, тож у повідомлення не потрапить чужий HTML;
* запис через UPSERT: оплата, VIP і free_clicks не губляться (старий їх стирав);
* /restart більше не видаляє людину з бази, лише заново питає імʼя, прізвище й групу;
* якщо кнопка груп застаріла (бота перезапускали), просимо почати спочатку,
  а не реєструємо «Без імені Без прізвища», як робив старий;
* реєстрація працює лише в особистому чаті.
"""
from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from ..core.users import RegisterStatus, clean_group, clean_name
from ..tg.helpers import core_of
from ..tg.router import CallbackRouter
from ..tg.text_router import TextRouter, clear, expect
from .profile_view import profile_text

log = logging.getLogger(__name__)

# мітки очікування тексту
S_NAME, S_LAST, S_GROUP, S_GROUP_CUSTOM = "reg_name", "reg_last", "reg_group", "reg_group_custom"
K_FIRST, K_LAST = "reg_first_name", "reg_last_name"
OTHER = "Інша"
GROUP_ROWS = [["ФЕ-51"], ["ФБ-51", "ФБ-52"], ["ФБ-53", "ФБ-54"], ["ФБ-55", "ФБ-56"]]

NAME_HINT = ("⚠️ Тут потрібні лише літери (можна апостроф і дефіс), до 40 символів. "
             "Спробуй ще раз:")


def groups_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(g, callback_data=f"REG_GROUP:{g}") for g in row] for row in GROUP_ROWS]
    rows.append([InlineKeyboardButton("❓ Інша група", callback_data=f"REG_GROUP:{OTHER}")])
    return InlineKeyboardMarkup(rows)


def _is_private(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == ChatType.PRIVATE


def _forget(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(K_FIRST, None)
    context.user_data.pop(K_LAST, None)
    clear(context)


async def begin_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Початок: питаємо імʼя. Викликається з /start (немає профілю) і з /restart."""
    _forget(context)
    expect(context, S_NAME)
    await update.effective_message.reply_text("👋 Вітаю! Введіть ваше <b>ІМ'Я</b>:", parse_mode=ParseMode.HTML)


async def _finish(update: Update, context: ContextTypes.DEFAULT_TYPE, group: str) -> None:
    """Запис у базу. Імена беремо з user_data. Якщо їх немає, просимо почати спочатку."""
    user = update.effective_user
    first, last = context.user_data.get(K_FIRST), context.user_data.get(K_LAST)
    msg = update.effective_message
    if not first or not last:
        _forget(context)
        await msg.reply_text("⚠️ Дані реєстрації загубились (бота перезапускали). Почнімо спочатку: /start")
        return
    core = core_of(context)
    try:
        result = await core.users.aregister(user.id, first, last, group, user.username)
    except Exception:
        log.exception("Не вдалося зберегти реєстрацію user=%s", user.id)
        await msg.reply_text("❌ Не вдалося зберегти дані. Спробуй ще раз трохи згодом.")
        return
    if result.status is RegisterStatus.DUPLICATE_NAME:
        _forget(context)
        expect(context, S_NAME)
        await msg.reply_text("⚠️ Студент з таким іменем та прізвищем вже зареєстрований!\n"
                             "Введіть ваше <b>ІМ'Я</b> ще раз:", parse_mode=ParseMode.HTML)
        return
    _forget(context)
    await core.audit.alog(user.id, "register", group)
    await msg.reply_text(f"✅ Реєстрація успішна!\nВи ввійшли як <b>{html.escape(first)} {html.escape(last)}</b> "
                         f"(Група: {html.escape(group)}).", parse_mode=ParseMode.HTML)
    profile = await core.profiles.get(user.id)
    if profile:
        tier = core.access.get_tier(user.id, profile)
        await msg.reply_text(profile_text(profile, tier, user.first_name), parse_mode=ParseMode.HTML)


def register(app: Application, router: CallbackRouter, texts: TextRouter) -> None:
    @texts.on(S_NAME)
    async def on_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = clean_name(update.effective_message.text)
        if name is None:
            await update.effective_message.reply_text(NAME_HINT)
            return
        context.user_data[K_FIRST] = name
        expect(context, S_LAST)
        await update.effective_message.reply_text("📝 Відмінно! Тепер введіть ваше <b>ПРІЗВИЩЕ</b>:",
                                                  parse_mode=ParseMode.HTML)

    @texts.on(S_LAST)
    async def on_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        last = clean_name(update.effective_message.text)
        if last is None:
            await update.effective_message.reply_text(NAME_HINT)
            return
        context.user_data[K_LAST] = last
        expect(context, S_GROUP)
        await update.effective_message.reply_text("📋 Оберіть вашу <b>групу</b>:", reply_markup=groups_keyboard(),
                                                  parse_mode=ParseMode.HTML)

    @texts.on(S_GROUP)
    async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text("👆 Оберіть групу кнопкою вище (або «Інша група»).")

    @texts.on(S_GROUP_CUSTOM)
    async def on_group_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        group = clean_group(update.effective_message.text)
        if group is None:
            await update.effective_message.reply_text(
                "⚠️ Назва групи: великі літери, цифри й дефіс, 2-12 символів (наприклад ФІ-51). Ще раз:")
            return
        await _finish(update, context, group)

    @router.on("REG_GROUP:")
    async def on_group_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_private(update):
            return
        group = update.callback_query.data.split(":", 1)[1]
        if group == OTHER:
            expect(context, S_GROUP_CUSTOM)
            await update.callback_query.edit_message_text(
                "✍️ <b>Введіть назву вашої групи вручну:</b>\n<i>(Наприклад: ФІ-51 або ПІ-52)</i>",
                parse_mode=ParseMode.HTML)
            return
        valid = clean_group(group)
        if valid is None:  # кнопку підробили або вона з іншої версії бота
            await update.callback_query.answer("Невідома група", show_alert=True)
            return
        await _finish(update, context, valid)

    async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_private(update):
            return
        await update.effective_message.reply_text(
            "🔄 Починаємо реєстрацію заново. Рівень доступу та оплата залишаються.")
        await begin_registration(update, context)

    app.add_handler(CommandHandler("restart", cmd_restart))