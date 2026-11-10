"""Роутер тексту: бот слухає звичайні повідомлення ЛИШЕ тоді, коли чекає відповідь.

Замість величезного ConversationHandler зі станами-числами (79, 80, 81, 99...) кожна фіча
ставить собі мітку і пише обробник:

    texts.expect(context, "reg_name")          # «далі чекаю імʼя»

    @texts.on("reg_name")
    async def on_name(update, context): ...

Мітка лежить у context.user_data (у памʼяті, зникає при перезапуску бота).
Поки мітки немає, текст ігнорується, і цей роутер нічого не ламає.
Працює тільки в особистому чаті.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

AWAIT_KEY = "await"
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


class TextRouterError(RuntimeError):
    """Помилка реєстрації (дублікат мітки)."""


def expect(context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    context.user_data[AWAIT_KEY] = state


def clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(AWAIT_KEY, None)


class TextRouter:
    def __init__(self) -> None:
        self._routes: dict[str, Handler] = {}

    def on(self, state: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            if state in self._routes:
                raise TextRouterError(f"Мітку {state!r} уже зареєстровано")
            self._routes[state] = fn
            return fn
        return deco

    async def dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if context.user_data is None:
            return
        state = context.user_data.get(AWAIT_KEY)
        if not state:
            return
        handler = self._routes.get(state)
        if handler is None:
            log.warning("Невідома мітка очікування: %r. Скидаю.", state)
            clear(context)
            return
        await handler(update, context)

    def expect(self, context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
        if state not in self._routes:
            raise TextRouterError(f"Мітку {state!r} не зареєстровано")
        expect(context, state)

    def handler(self) -> MessageHandler:
        return MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, self.dispatch)
