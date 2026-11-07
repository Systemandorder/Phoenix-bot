"""Configuration: the single place that reads environment variables.

Rule: no secrets, hosts or chat IDs hard-coded anywhere else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _required(name: str) -> str:
    value = _str(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def _int(name: str, default: int | None = None) -> int:
    raw = _str(name)
    if not raw:
        if default is None:
            raise ConfigError(f"Required environment variable {name} is not set")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _int_set(name: str) -> frozenset[int]:
    out: set[int] = set()
    for part in _str(name).replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.add(int(part))
            except ValueError as exc:
                raise ConfigError(f"{name} contains non-integer {part!r}") from exc
    return frozenset(out)


def _bool(name: str, default: bool = False) -> bool:
    return _str(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # required
    bot_token: str = field(repr=False)
    admin_id: int
    master_key: str = field(repr=False)
    # core
    data_dir: Path
    db_path: Path
    timezone: str
    home_group: str
    vip_ids: frozenset[int]
    wrap_user_keys: bool
    log_level: str
    # optional integrations
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
        return self.data_dir / "tasks"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(_str("DATA_DIR", ".")).resolve()
        return cls(
            bot_token=_required("BOT_TOKEN"),
            admin_id=_int("ADMIN_ID"),
            master_key=_required("MASTER_KEY"),
            data_dir=data_dir,
            db_path=Path(_str("DB_PATH") or data_dir / "botsort.db"),
            timezone=_str("TIMEZONE", "Europe/Kyiv"),
            home_group=_str("HOME_GROUP", "ФБ-56"),
            vip_ids=_int_set("VIP_IDS"),
            wrap_user_keys=_bool("WRAP_USER_KEYS"),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
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
    return Settings.from_env()
