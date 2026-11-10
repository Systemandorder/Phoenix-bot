"""Збирання Telegram-застосунку. Запуск:  python -m phoenix"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, ContextTypes, Defaults

from .config import Settings, get_settings
from .core.bootstrap import Core, build_core
from .features import register_all
from .logging_setup import setup_logging
from .tg.router import CallbackRouter
from .tg.text_router import TextRouter

log = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Одне місце для всіх помилок: у журнал (токени сховано), користувачу коротко."""
    log.error("Помилка в обробнику", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.answer("⚠️ Щось пішло не так. Спробуй ще раз.", show_alert=True)
        except BadRequest:
            pass


def build_application(core: Core) -> Application:
    s: Settings = core.settings
    app = (ApplicationBuilder()
           .token(s.bot_token)
           .defaults(Defaults(tzinfo=ZoneInfo(s.timezone)))
           .build())
    app.bot_data["core"] = core
    router, texts = CallbackRouter(), TextRouter()
    register_all(app, router, texts)
    app.add_handler(router.handler())
    app.add_handler(texts.handler())
    app.add_error_handler(on_error)
    return app


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.data_dir / "logs" / "phoenix.log")
    core = build_core(settings)
    try:
        build_application(core).run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        core.close()


if __name__ == "__main__":
    main()
