"""Дрібні помічники для обробників."""
from __future__ import annotations

from telegram.ext import ContextTypes

from ..core.bootstrap import Core


def core_of(context: ContextTypes.DEFAULT_TYPE) -> Core:
    """Ядро (база, права, аудит) з контексту застосунку."""
    return context.application.bot_data["core"]