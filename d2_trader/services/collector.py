"""The collection jobs. Each one is a single pass over one source."""

import random
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.config import Settings
from d2_trader.markets import MarketKey
from d2_trader.models import Item, WatchItem
from d2_trader.seasons import season_events
from d2_trader.services.calendar import apply_date, find_disagreements, load_seasons
from d2_trader.services.catalog import get_or_create_market, market_key, upsert_catalog
from d2_trader.services.fg_rates import fg_markets
from d2_trader.services.notify import Message, deliver
from d2_trader.services.pricing import store_listings
from d2_trader.services.rates import store_derived_rates
from d2_trader.services.rune_values import fit_market, rune_item
from d2_trader.services.watch import enabled_watches
from d2_trader.sources import blizzard, diablo2io, traderie
from d2_trader.sources.http import Pace, PoliteClient, RobotsDenied, SourceBlocked


class SourceDisabled(Exception):
    """This source is switched off in settings, so no request was made."""


@dataclass(frozen=True)
class Target:
    item: Item
    market: MarketKey


def _client(settings: Settings, interval: float, transport=None) -> PoliteClient:
    pace = Pace(interval, settings.pace_jitter)
    return PoliteClient(user_agent=settings.user_agent, pace=pace, transport=transport)


def shuffled[T](things: list[T]) -> list[T]:
    """A new order each run, so a poll never repeats the same sequence of requests."""
    return random.sample(things, len(things))


def watched_items(watches: list[WatchItem]) -> list[Item]:
    return list({w.item.id: w.item for w in watches}.values())


def watched_targets(watches: list[WatchItem]) -> list[Target]:
    """Each (item, market) once, however many watchlists hold it."""
    pairs = {(w.item.id, market_key(w.market)): w.item for w in watches}
    return [Target(item, market) for (_, market), item in pairs.items()]


async def sync_catalog(
    session: AsyncSession, settings: Settings, now: datetime, transport=None
) -> int:
    if not settings.diablo2io_enabled:
        raise SourceDisabled("diablo2.io is switched off")
    async with _client(settings, settings.diablo2io_min_interval_s, transport) as client:
        return await upsert_catalog(session, await diablo2io.fetch_catalog(client))


async def collect_diablo2io(
    session: AsyncSession, settings: Settings, now: datetime, transport=None
) -> int:
    if not settings.diablo2io_enabled:
        raise SourceDisabled("diablo2.io is switched off")
    stored = 0
    items = watched_items(await enabled_watches(session))
    pages = [(item.id, item.diablo2io_path) for item in items if item.diablo2io_path]
    async with _client(settings, settings.diablo2io_min_interval_s, transport) as client:
        for item_id, path in pages:
            sales = await diablo2io.fetch_sold(client, path)
            stored += await store_listings(session, item_id, sales, now)
    return stored


async def _traderie_id(client: PoliteClient, item: Item) -> str | None:
    if item.traderie_id is None:
        item.traderie_id = await traderie.find_item_id(client, item.name)
    return item.traderie_id


async def collect_traderie(
    session: AsyncSession, settings: Settings, now: datetime, transport=None
) -> int:
    if not settings.traderie_enabled:
        raise SourceDisabled("off: set TRADERIE_ENABLED=true to collect (see README)")
    stored = 0
    targets = shuffled(watched_targets(await enabled_watches(session)))
    async with _client(settings, settings.traderie_min_interval_s, transport) as client:
        for target in targets:
            remote_id = await _traderie_id(client, target.item)
            if remote_id is None:
                continue
            for kind in traderie.KIND_PARAMS:
                listings = await traderie.fetch_listings(client, remote_id, target.market, kind)
                stored += await store_listings(session, target.item.id, listings, now)
    return stored


RUNE_KINDS = ("sold", "ask")


async def collect_rune_listings(
    session: AsyncSession, client: PoliteClient, market: MarketKey, now: datetime
) -> int:
    """Store a market's latest sales of every rune Traderie lists by id, and its open asks.

    Sales feed the rune model; asks are the cheapest listings the Runes page shows.
    """
    stored = 0
    pairs = [(r, i, kind) for r, i in traderie.RUNE_ITEM_IDS.items() for kind in RUNE_KINDS]
    for rune, remote_id, kind in shuffled(pairs):
        item = await rune_item(session, rune, remote_id)
        listings = await traderie.fetch_listings(client, remote_id, market, kind)
        stored += await store_listings(session, item.id, listings, now)
    return stored


async def refresh_rates(
    session: AsyncSession, settings: Settings, now: datetime, transport=None
) -> int:
    """Collect rune trades and asks, then re-fit every rune's value from all the trades.

    For the dashboard's default market, so rune values exist before anything is watched;
    every watched market; and every market with rune prices in fg, since the fg rate is
    measured against these values. See `rune_model.py` for how.
    """
    if not settings.traderie_enabled:
        raise SourceDisabled("off: rates come from Traderie trades (see README)")
    default = await get_or_create_market(session, MarketKey())
    markets = {default.id: MarketKey()}
    markets |= {w.market_id: market_key(w.market) for w in await enabled_watches(session)}
    markets |= dict(await fg_markets(session, now))
    seasons = await load_seasons(session)
    stored = 0
    async with _client(settings, settings.traderie_min_interval_s, transport) as client:
        for market_id, market in markets.items():
            await collect_rune_listings(session, client, market, now)
            fit = await fit_market(session, market, seasons, now)
            stored += await store_derived_rates(session, market_id, fit.today(), fit.direct)
    return stored


async def _official_dates(session: AsyncSession, settings, now, transport):
    async with _client(settings, settings.blizzard_min_interval_s, transport) as client:
        dates, article = await blizzard.fetch_latest_schedule(client, now)
    for date in dates:
        await apply_date(
            session, date.number, date.kind, date.at, "blizzard", article.url if article else None
        )
    return dates


async def _banner_dates(session: AsyncSession, settings, now, transport):
    """The cross-check. Its failure must not cost the official dates already in hand."""
    if not settings.diablo2io_enabled:
        return []
    try:
        async with _client(settings, settings.diablo2io_min_interval_s, transport) as client:
            banner = await diablo2io.fetch_banner(client, now)
    except (SourceBlocked, RobotsDenied, httpx.HTTPError):
        return []
    for date in banner:
        await apply_date(session, date.number, date.kind, date.at, "diablo2io")
    return banner


async def _report_disagreements(session, settings, official, banner) -> None:
    for conflict in find_disagreements(official, banner):
        await deliver(
            session,
            Message(
                kind="source",
                dedupe_key=(
                    f"season-conflict:{conflict.number}:{conflict.kind}:"
                    f"{conflict.blizzard:%Y%m%d}:{conflict.diablo2io:%Y%m%d}"
                ),
                title=f"Season {conflict.number} dates disagree",
                body=(
                    f"Blizzard says {conflict.kind} {conflict.blizzard:%b %d %H:%M} UTC, "
                    f"diablo2.io says {conflict.diablo2io:%b %d %H:%M} UTC. Using Blizzard's."
                ),
                url="/seasons",
            ),
            settings,
        )


async def sync_seasons(
    session: AsyncSession, settings: Settings, now: datetime, transport=None
) -> int:
    """Refresh the calendar. A run that learns nothing from Blizzard is a failure."""
    official = await _official_dates(session, settings, now, transport)
    banner = await _banner_dates(session, settings, now, transport)
    await _report_disagreements(session, settings, official, banner)
    await send_season_events(session, settings, now)
    if not official:
        raise ValueError("no schedule found on Blizzard's news: the article format may have moved")
    return len(official)


async def send_season_events(session: AsyncSession, settings: Settings, now: datetime) -> int:
    sent = 0
    for event in season_events(now, await load_seasons(session)):
        message = Message("season", f"season:{event.key}", event.title, event.body, "/seasons")
        sent += await deliver(session, message, settings)
    return sent
