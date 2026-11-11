"""Runtime settings, read from the environment and `.env`. See `.env.example`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # Off in code, so a fresh clone sends Traderie nothing. Its Terms of Service forbid
    # automated access; the operator turns it on in `.env`. See README, "Data sources".
    traderie_enabled: bool = False
    diablo2io_enabled: bool = True

    # Honest on purpose. A source that refuses this client is recorded as blocked; it is
    # never retried under a browser's name.
    user_agent: str = "d2-trader/0.1 (personal price tracker)"

    traderie_min_interval_s: float = 5.0
    # diablo2.io asks for 60s between requests to its public API; its pages get the same.
    diablo2io_min_interval_s: float = 60.0
    blizzard_min_interval_s: float = 5.0
    # Each gap is stretched by a random 0-100% on top of its minimum, so Traderie sees one
    # request every 5-10 seconds rather than a metronome.
    pace_jitter: float = 1.0

    traderie_poll_minutes: int = 60
    # diablo2.io caches its "sold" block for a day, so polling harder learns nothing.
    diablo2io_poll_hours: int = 24
    season_poll_hours: int = 6
    catalog_poll_days: int = 7
    rates_poll_hours: int = 12

    # How long an ask stays live after a collector last saw it.
    ask_stale_hours: int = 6

    discord_webhook_url: str | None = None
    ntfy_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
