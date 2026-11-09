"""Фічі бота. Кожен модуль має  register(app, router, texts)  і нічого не знає про інші модулі."""
from __future__ import annotations

from telegram.ext import Application

from ..tg.router import CallbackRouter
from ..tg.text_router import TextRouter
from . import registration, start


def register_all(app: Application, router: CallbackRouter, texts: TextRouter) -> None:
    registration.register(app, router, texts)
    start.register(app, router, texts)