"""Конфігурація: єдине місце, де читаються змінні середовища.

Правило: жодних токенів, хостів чи ID чатів у коді — тільки тут і в .env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Читаємо .env з поточної папки. Справжні змінні середовища мають пріоритет.
load_dotenv(override=False)

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(RuntimeError):
    """Обовʼязкове налаштування відсутнє або некоректне."""


# ---------- дрібні читачі змінних ----------
def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _required(name: str) -> str:
    value = _str(name)
    if not value:
        raise ConfigError(f"Не задано обовʼязкову змінну {name} (див. .env.example)")
    return value


def _int(name: str, default: int | None = None) -> int:
    raw = _str(name)
    if not raw:
        if default is None:
            raise ConfigError(f"Не задано обовʼязкову змінну {name} (див. .env.example)")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} має бути цілим числом, отримано {raw!r}") from exc


def _int_set(name: str) -> frozenset[int]:
    out: set[int] = set()
    for part in _str(name).replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.add(int(part))
            except ValueError as exc:
                raise ConfigError(f"{name} містить не число: {part!r}") from exc
    return frozenset(out)


def _bool(name: str, default: bool = False) -> bool:
    return _str(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


# ---------- перевірки значень ----------
def _check_master_key(key: str) -> str:
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            "MASTER_KEY некоректний. Згенеруй: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc
    return key


def _check_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"TIMEZONE {name!r} невідома (потрібен пакет tzdata?)") from exc
    return name


def _check_log_level(level: str) -> str:
    if level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL має бути одним із {sorted(_LOG_LEVELS)}, отримано {level!r}")
    return level


@dataclass(frozen=True)
class Settings:
    # --- обовʼязкові ---
    bot_token: str = field(repr=False)  # repr=False: токен не потрапить у логи
    admin_id: int
    master_key: str = field(repr=False)
    # --- ядро ---
    data_dir: Path
    db_path: Path
    timezone: str
    home_group: str
    vip_ids: frozenset[int]
    wrap_user_keys: bool
    log_level: str
    # --- необовʼязкові інтеграції ---
    alerts_api_token: str = field(default="", repr=False)
    virustotal_api_key: str = field(default="", repr=False)
    tg_api_id: int = 0
    tg_api_hash: str = field(default="", repr=False)
    sms_public_url: str = ""
    radar_api_url: str = ""
    cyber_ai_url: str = ""
    warp_proxy: str = ""
    tts_api_url: str = ""
    medic_api_url: str = ""
    zoom_api_url: str = ""
    worker_url: str = ""

    @property
    def files_dir(self) -> Path:
        """Папка з файлами бота."""
        return self.data_dir / "tasks"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(_str("DATA_DIR", ".")).resolve()
        admin_id = _int("ADMIN_ID")
        if admin_id <= 0:
            raise ConfigError("ADMIN_ID має бути додатним числом (твій Telegram ID)")
        return cls(
            bot_token=_required("BOT_TOKEN"),
            admin_id=admin_id,
            master_key=_check_master_key(_required("MASTER_KEY")),
            data_dir=data_dir,
            db_path=Path(_str("DB_PATH") or data_dir / "botsort.db"),
            timezone=_check_timezone(_str("TIMEZONE", "Europe/Kyiv")),
            home_group=_str("HOME_GROUP", "ФБ-56"),
            vip_ids=_int_set("VIP_IDS"),
            wrap_user_keys=_bool("WRAP_USER_KEYS"),
            log_level=_check_log_level(_str("LOG_LEVEL", "INFO").upper()),
            alerts_api_token=_str("ALERTS_API_TOKEN"),
            virustotal_api_key=_str("VIRUSTOTAL_API_KEY"),
            tg_api_id=_int("TG_API_ID", 0),
            tg_api_hash=_str("TG_API_HASH"),
            sms_public_url=_str("SMS_PUBLIC_URL"),
            radar_api_url=_str("RADAR_API_URL"),
            cyber_ai_url=_str("CYBER_AI_URL"),
            warp_proxy=_str("WARP_PROXY"),
            tts_api_url=_str("TTS_API_URL"),
            medic_api_url=_str("MEDIC_API_URL"),
            zoom_api_url=_str("ZOOM_API_URL"),
            worker_url=_str("WORKER_URL"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Налаштування читаються один раз за запуск."""
    return Settings.from_env()
