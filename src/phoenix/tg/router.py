"""Роутер кнопок: замінює один «ловець усього» (handle_button pattern='.*') у старому боті.

Кожна фіча реєструє свій префікс callback_data:

    @router.on("CAL_DAY:")
    async def cal_day(update, context): ...

Правила:
* підходить найдовший збіг префікса, тож "CAL_NAV:" не плутається з "CAL:";
* однакових префіксів двічі не буває (помилка під час запуску, а не дивна поведінка в чаті);
* для невідомої або застарілої кнопки користувач бачить пояснення, а не вічне «годинникове» коло;
* за замовчуванням роутер сам робить query.answer() ДО обробника. Якщо обробник хоче показати
  спливаюче вікно, реєструй з answer=False і виклич query.answer(text, show_alert=True) сам.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

STALE_TEXT = "Ця кнопка застаріла. Відкрий меню знову: /start"


class RouterError(RuntimeError):
    """Помилка реєстрації маршруту (дублікат, порожній префікс)."""


@dataclass(frozen=True, slots=True)
class Route:
    prefix: str
    handler: Handler
    answer: bool


class CallbackRouter:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    # ---------- реєстрація ----------
    def add(self, prefix: str, handler: Handler, *, answer: bool = True) -> None:
        if not prefix:
            raise RouterError("Порожній префікс заборонений: він перехопив би всі кнопки")
        if prefix in self._routes:
            raise RouterError(f"Префікс {prefix!r} уже зареєстровано")
        self._routes[prefix] = Route(prefix, handler, answer)

    def on(self, prefix: str, *, answer: bool = True) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.add(prefix, fn, answer=answer)
            return fn
        return deco

    # ---------- пошук ----------
    def resolve(self, data: str) -> Route | None:
        best: Route | None = None
        for prefix, route in self._routes.items():
            if data.startswith(prefix) and (best is None or len(prefix) > len(best.prefix)):
                best = route
        return best

    @property
    def prefixes(self) -> list[str]:
        return sorted(self._routes)

    # ---------- виконання ----------
    async def dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        route = self.resolve(data)
        if route is None:
            log.warning("Невідома кнопка: %r (user=%s)", data[:64],
                        update.effective_user and update.effective_user.id)
            with suppress(BadRequest):
                await query.answer(STALE_TEXT, show_alert=True)
            return
        if route.answer:
            with suppress(BadRequest):  # query міг застаріти. Це не привід падати
                await query.answer()
        await route.handler(update, context)

    def handler(self) -> CallbackQueryHandler:
        """Єдиний CallbackQueryHandler, який додається в Application."""
        return CallbackQueryHandler(self.dispatch)
