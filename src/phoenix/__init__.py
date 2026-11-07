"""Phoenix — модульне ядро Telegram-бота Botsort."""

__version__ = "0.1.0"

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
