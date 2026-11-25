"""Rune values per market, from the rune trades stored in `listings`.

Rune sales are kept like any other listing, so the model sees every trade since the
season began rather than only the latest page Traderie returns, and correcting the model
re-reads all of them.

This reads `listings` directly rather than through `market_view`, because it needs every
rune at once and not one item's summary. It keeps the same boundary: a ladder market reads
nothing from before its season began, and a non-ladder one nothing from before the last
transfer (or `HISTORY_DAYS`, if none is known). Asks and bids are read only while a collector
still sees them up, so none from an earlier season can appear either.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import (
    GOODS,
    RUNES,
    SEED_RATES,
    Alternative,
    alternative_value,
    normalize_currency,
    unit_value,
)
from d2_trader.markets import MarketKey
from d2_trader.models import CurrencyRate, Item, Listing, Market
from d2_trader.rune_model import RuneFit, Trade, fit_runes, trade_from
from d2_trader.seasons import Season, reset_days, season_floor, season_state
from d2_trader.services.catalog import comparable_market_ids, get_item_by_name, market_key
from d2_trader.services.fg_rates import rune_item_names
from d2_trader.stats import drop_outliers

HISTORY_DAYS = 120
CHEAPEST = 10
TRADED = frozenset((*RUNES, *GOODS))


async def rune_item(session: AsyncSession, rune: str, traderie_id: str | None = None) -> Item:
    """The catalog's row for a rune, under either name, made if the catalog lacks it.

    A new one is named as diablo2.io names it ("Lo"), so a later catalog sync updates
    that row instead of adding a second.
    """
    item = await get_item_by_name(session, rune) or await get_item_by_name(session, f"{rune} rune")
    if item is None:
        item = Item(name=rune.title(), category="misc")
        session.add(item)
    if traderie_id and not item.traderie_id:
        item.traderie_id = traderie_id
    await session.flush()
    return item


def model_floor(seasons: list[Season], ladder: bool, now: datetime) -> datetime:
    """The oldest trade a market's rune values may draw on."""
    floor = season_floor(season_state(now, seasons), ladder)
    return floor or now - timedelta(days=HISTORY_DAYS)


async def sold_rows(
    session: AsyncSession, market: MarketKey, since: datetime, names: Sequence[str]
) -> list[tuple[str, Listing]]:
    """Every completed sale of the named items (lower case) in a market since a moment."""
    query = (
        select(Item.name, Listing)
        .join(Item, Item.id == Listing.item_id)
        .where(
            func.lower(Item.name).in_(names),
            Listing.market_id.in_(await comparable_market_ids(session, market)),
            Listing.kind == "sold",
            Listing.observed_at >= since,
        )
    )
    return [(name, listing) for name, listing in (await session.execute(query)).all()]


async def rune_trades(session: AsyncSession, market: MarketKey, since: datetime) -> list[Trade]:
    """Every completed rune-for-rune sale in a market since a moment."""
    trades = []
    for name, listing in await sold_rows(session, market, since, rune_item_names()):
        rune = normalize_currency(name)
        day = listing.observed_at.date()
        trade = trade_from(rune, day, listing.quantity, listing.alternatives)
        if trade is not None:
            trades.append(trade)
    return trades


async def fit_market(
    session: AsyncSession, market: MarketKey, seasons: list[Season], now: datetime
) -> RuneFit:
    """Every rune's value in one market, day by day, from its start to today."""
    floor = model_floor(seasons, market.ladder, now)
    trades = await rune_trades(session, market, floor)
    breaks = reset_days(seasons, market.ladder)
    return fit_runes(trades, SEED_RATES, until=now.date(), start=floor.date(), breaks=breaks)


@dataclass(frozen=True)
class Ask:
    """One way to get a rune: somebody has it, or wants another rune and pays in it."""

    listing: Listing
    value: float  # Ist per rune, at today's rune values.
    wanted: str | None = None  # For a bid, the rune its poster wants...
    offered: Alternative | None = None  # ...and what they pay, this rune included.


@dataclass(frozen=True)
class RuneAsks:
    cheapest: list[Ask]
    hidden: int  # Bait or joke prices left out.
    seen_at: datetime | None  # When a collector last saw the freshest of them.


def bid_offers(wanted: str, listing: Listing, rates: Mapping[str, float]) -> list[tuple[str, Ask]]:
    """A bid from the other side: "wants 2 Ist, has 5 Um" sells Um at 0.4 Ist each.

    Each alternative is its own offer, since whoever fills the bid picks one. A bundle is
    listed under its most valuable rune, its cost split in proportion to value, so that
    rune shows the whole bundle's discount. One holding anything but runes and goods is
    left out, as too roughly valued, and so is one paying in what it wants. A good counts
    only if `rates` has it, and the pages' rates hold no unmeasured good.
    """
    cost = alternative_value([(wanted, max(listing.quantity, 1))], rates)
    if not cost:
        return []
    cheapest: dict[str, Ask] = {}
    for alternative in listing.alternatives:
        lots = [(str(c), float(q)) for c, q in alternative]
        if any(c not in TRADED or c == wanted or q <= 0 for c, q in lots):
            continue
        worth = alternative_value(lots, rates)
        if not worth:
            continue
        main = max(lots, key=lambda lot: lot[1] * rates[lot[0]])[0]
        offer = Ask(listing, rates[main] * cost / worth, wanted, lots)
        if main not in cheapest or offer.value < cheapest[main].value:
            cheapest[main] = offer
    return list(cheapest.items())


def rank_asks(
    rows: Iterable[tuple[str, Listing]], rates: Mapping[str, float], limit: int = CHEAPEST
) -> dict[str, RuneAsks]:
    """Each rune's cheapest offers per rune, from asks and bids alike, bait left out.

    A seller takes whichever alternative is offered, so an ask is worth its cheapest. A
    bait price ("Zod for 1 El") would top every list, so asks far from the rest of the
    rune's asks are dropped as the dashboard's lowest ask drops them, and counted.
    """
    by_rune: dict[str, list[Ask]] = {}
    for name, listing in rows:
        if listing.kind == "bid":
            for rune, offer in bid_offers(normalize_currency(name), listing, rates):
                by_rune.setdefault(rune, []).append(offer)
            continue
        value = unit_value(listing.alternatives, rates, "ask", listing.quantity)
        if value is not None and value > 0:
            by_rune.setdefault(normalize_currency(name), []).append(Ask(listing, value))
    ranked = {}
    for rune, asks in by_rune.items():
        kept = set(drop_outliers([a.value for a in asks]))
        honest = sorted((a for a in asks if a.value in kept), key=lambda a: a.value)
        seen = max(a.listing.last_seen_at for a in asks)
        ranked[rune] = RuneAsks(honest[:limit], len(asks) - len(honest), seen)
    return ranked


async def active_asks(
    session: AsyncSession,
    market: MarketKey,
    seen_since: datetime,
    names: Sequence[str] | None = None,
) -> list[tuple[str, Listing]]:
    """Every ask and bid for the named items (runes, if none) seen up since `seen_since`."""
    query = (
        select(Item.name, Listing)
        .join(Item, Item.id == Listing.item_id)
        .where(
            func.lower(Item.name).in_(rune_item_names() if names is None else names),
            Listing.market_id.in_(await comparable_market_ids(session, market)),
            Listing.kind.in_(("ask", "bid")),
            Listing.last_seen_at >= seen_since,
        )
    )
    return [(name, listing) for name, listing in (await session.execute(query)).all()]


async def measured_markets(session: AsyncSession) -> list[MarketKey]:
    """The default market, then every other market the rates job has measured."""
    query = (
        select(Market)
        .join(CurrencyRate, CurrencyRate.market_id == Market.id)
        .where(CurrencyRate.source == "derived")
        .distinct()
        .order_by(Market.id)
    )
    found = [market_key(row) for row in (await session.execute(query)).scalars()]
    return [MarketKey(), *(m for m in found if m != MarketKey())]


def report(fit: RuneFit, rune: str, market: MarketKey, days: int = 14) -> list[str]:
    """One rune's value today and over the last `days`, as lines for a terminal."""
    path = fit.paths.get(rune)
    if not path:
        return [f"{market.label}: no rune trades stored yet; run `python -m d2_trader run rates`."]
    today, values = path[-1], fit.today()
    others = ", ".join(
        f"{today.value / values[r]:.3g} {r.title()}" for r in ("ber", "ist", "ohm") if r != rune
    )
    lines = [
        f"{rune.title()} · {market.label} · {today.day:%Y-%m-%d}",
        f"  {today.value:.2f} Ist  (95%: {today.low:.2f} to {today.high:.2f})  = {others}",
        f"  from {fit.trades} rune trades since {path[0].day:%Y-%m-%d}; "
        f"{fit.direct.get(rune, 0)} name {rune.title()}, the rest value it through the others",
        "",
        "  day          Ist    95% interval    trades naming it",
    ]
    lines += [
        f"  {p.day:%Y-%m-%d}  {p.value:6.2f}  {p.low:6.2f} to {p.high:6.2f}  {p.observations:4d}"
        for p in path[-days:]
    ]
    return lines
