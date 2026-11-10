"""Головне меню в Telegram. Кнопки і правила взято зі старого бота, а розділи, які ще не перенесено,
відповідають «🚧 розділ ще переноситься», а не мовчать і не пишуть «кнопка застаріла».

Модуль реєструється ОСТАННІМ: він ставить заглушки лише на ті кнопки, яких ще не зайняв
жоден перенесений розділ. Коли розділ переїде, його власний обробник просто займе префікс.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import suppress

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes

from ..core.access import Profile, path_uid
from ..core.bootstrap import Core
from ..tg.helpers import core_of
from ..tg.router import CallbackRouter
from ..tg.text_router import TextRouter
from . import menu_view
from .profile_view import profile_text

log = logging.getLogger(__name__)

COMING_SOON = "🚧 Цей розділ ще переноситься зі старого бота. Скоро буде."
NEED_REGISTER = "Спочатку потрібна реєстрація. Напиши /start"


def _read_user_extras(core: Core, user_id: int) -> tuple[int, str]:
    """Скільки в людини «Важливого» і яка в неї тема. Лише читання.
    Якщо таблиці розділу немає (модуль вимкнено), меню не падає, а рахує нуль."""
    count = 0
    for table in ("bkb_fav", "bkb_favx"):                     # назви таблиць це константи, не ввід людини
        try:
            row = core.db.query_one(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,))
        except sqlite3.OperationalError:
            continue
        count += row[0] if row else 0
    try:
        row = core.db.query_one("SELECT ui_theme FROM users WHERE user_id = ?", (user_id,))
    except sqlite3.OperationalError:
        row = None
    return count, (row[0] if row and row[0] else "DEFAULT")


def build_rows(core: Core, user_id: int, profile: Profile | None, tier: int,
               move_mode: bool = False) -> list[list[menu_view.Btn]]:
    fav_count, theme = _read_user_extras(core, user_id)
    folder_id = path_uid(str(core.settings.files_dir))
    cat = menu_view.catalog(folder_id=folder_id, fav_count=fav_count, theme=theme)
    layout = core.menu.layout(menu_view.available_codes(cat, tier, move_mode), user_id,
                              profile.group_name if profile else None,
                              user_id == core.settings.admin_id, tier, move_mode)
    return menu_view.build_rows(layout, cat)


def to_markup(rows: list[list[menu_view.Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(b.label, callback_data=b.data) for b in row]
                                 for row in rows])


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    user, msg = update.effective_user, update.effective_message
    if user is None or msg is None:
        return
    core = core_of(context)
    profile = await core.profiles.get(user.id)
    if profile is None:
        await msg.reply_text(NEED_REGISTER)
        return
    tier = core.access.get_tier(user.id, profile)
    rows = await asyncio.to_thread(build_rows, core, user.id, profile, int(tier))
    text, markup = profile_text(profile, tier, user.first_name), to_markup(rows)
    query = getattr(update, "callback_query", None) if edit else None
    if query is not None:
        with suppress(BadRequest):                              # «повідомлення не змінилось» не помилка
            await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return
    await msg.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def _go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_main_menu(update, context, edit=True)


async def _coming_soon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        with suppress(BadRequest):
            await query.answer(COMING_SOON, show_alert=True)


async def _ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    return None


def _add_once(router: CallbackRouter, prefix: str, handler, *, answer: bool = True) -> bool:
    if prefix in router.prefixes:
        return False
    router.add(prefix, handler, answer=answer)
    return True


def register(app: Application, router: CallbackRouter, texts: TextRouter) -> None:
    for prefix in ("MENU_HOME", "ROOT_ID", "REFRESH_FOLDER"):
        _add_once(router, prefix, _go_home)
    _add_once(router, "IGNORE", _ignore)
    for prefix in menu_view.all_prefixes():
        _add_once(router, prefix, _coming_soon, answer=False)   # відповідь з вікном дає сама заглушка
