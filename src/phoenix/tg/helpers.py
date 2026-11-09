"""Дрібні помічники для обробників."""
from __future__ import annotations

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..core.bootstrap import Core


def core_of(context: ContextTypes.DEFAULT_TYPE) -> Core:
    """Ядро (база, права, аудит) з контексту застосунку."""
    return context.application.bot_data["core"]


async def edit_or_send(update: Update, text: str, markup=None, *, parse_mode: str = "HTML") -> None:
    """Для кнопок міняємо те саме повідомлення (екран «оновлюється»), для команд шлемо нове.
    «Нічого не змінилось» від Telegram не вважаємо помилкою."""
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
        return
    await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)