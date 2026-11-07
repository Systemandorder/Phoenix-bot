"""Logging with Telegram bot token redaction."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_TOKEN_RE = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{30,}")


class RedactFilter(logging.Filter):
    """Masks bot tokens (they appear in Telegram API URLs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "bot" in message:
            record.msg = _TOKEN_RE.sub("bot<redacted>", message)
            record.args = ()
        return True


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(RedactFilter())  # on handlers: also covers child loggers
    logging.basicConfig(level=getattr(logging, level, logging.INFO), handlers=handlers, force=True)
    for noisy in ("httpx", "httpcore", "telegram", "telethon"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
