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
    # Rune trades and the rune asks the Runes page lists. Hourly by the operator's choice
    # (2026-11-23): a cheap ask sells within minutes, so one fetched 12 hours ago is gone.
    rates_poll_hours: int = 1
    # Gems, shards, keys and the like: about 90 requests a market, more than twice what the
    # runes cost, so less often. Their offers on the Currencies page are up to this old.
    goods_poll_hours: int = 3

    # How long an ask stays live after a collector last saw it.
    ask_stale_hours: int = 6
    # The oldest an open ask or bid may be, by when it was posted or last edited, and
    # still count. Traderie keeps listings up for months; a 271-day-old ask still shown
    # is a price nobody has moved to, not today's market. The operator's choice (2026-11-25).
    listing_max_age_hours: int = 24

    # The operator's own Traderie username (2026-11-26): offers they posted are not
    # offers they can take, so the Runes and Trade up pages leave them out. Empty keeps all.
    traderie_username: str = "rakanishoes"

    discord_webhook_url: str | None = None
    ntfy_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
