"""Журнали, у яких токени і паролі автоматично замінюються на заглушку.

Захист працює на рівні готового тексту, тому маскуються і звичайні повідомлення,
і повні тексти помилок (traceback), де токен теж може опинитись у адресі запиту.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Токен Telegram бота виглядає як bot123456789:AAA... і трапляється в адресах запитів
_TOKEN_RE = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{30,}")
# Загальні пари на кшталт token=..., password=..., api_hash=...
_SECRET_RE = re.compile(r"(?i)\b(token|password|passwd|secret|api_hash|api_key)\b(\s*[=:]\s*)([^\s,;'\"&]+)")


def redact(text: str) -> str:
    """Замінює токени та паролі в тексті на заглушку."""
    text = _TOKEN_RE.sub("bot<redacted>", text)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)


class RedactingFormatter(logging.Formatter):
    """Форматувальник, який чистить готовий рядок разом із текстом помилки."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Налаштовує журнали в консоль і, за потреби, у файл із ротацією (до 5 копій по 5 МБ)."""
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=handlers, force=True)
    # Ці бібліотеки надто балакучі, залишаємо лише попередження
    for noisy in ("httpx", "httpcore", "telegram", "telethon"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
