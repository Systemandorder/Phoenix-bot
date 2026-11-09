"""/start і /ping.

Є профіль -> показуємо хто ти (головне меню зʼявиться на наступному кроці).
Немає профілю -> починаємо реєстрацію.
"""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from ..tg.helpers import core_of
from ..tg.router import CallbackRouter
from ..tg.text_router import TextRouter
from .profile_view import profile_text
from .registration import begin_registration


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg, chat = update.effective_user, update.effective_message, update.effective_chat
    if user is None or msg is None or chat is None:
        return
    if chat.type != ChatType.PRIVATE:
        await msg.reply_text("👋 Напиши мені в особисті повідомлення: /start")
        return
    core = core_of(context)
    profile = await core.profiles.get(user.id)
    if profile is None:
        await begin_registration(update, context)
        return
    await core.users.atouch_username(user.id, user.username)
    tier = core.access.get_tier(user.id, profile)
    await msg.reply_text(profile_text(profile, tier, user.first_name), parse_mode=ParseMode.HTML)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("pong 🏓")


def register(app: Application, router: CallbackRouter, texts: TextRouter) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))