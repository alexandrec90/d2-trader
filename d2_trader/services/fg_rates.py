"""Forum gold rates from stored listings: rune prices in fg against rune values in Ist."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import RUNES, normalize_currency
from d2_trader.forum_gold import FgQuote, Valuation, fg_history
from d2_trader.markets import MarketKey
from d2_trader.models import Item, Listing, Market
from d2_trader.seasons import Season, reset_days
from d2_trader.services.catalog import comparable_market_ids, market_key
from d2_trader.services.rates import load_rates, manual_currencies

HISTORY_DAYS = 120


def fg_price(listing: Listing) -> float | None:
    """The fg one rune of this listing went for, if one alternative was plain forum gold."""
    for alternative in listing.alternatives:
        if len(alternative) == 1 and alternative[0][0] == "fg":
            return alternative[0][1] / max(listing.quantity, 1)
    return None


async def fg_quotes(session: AsyncSession, market: MarketKey, since: datetime) -> list[FgQuote]:
    """Every rune price in fg for a market. A buyer's offer is left out: it is not a price."""
    query = (
        select(Listing, Item.name)
        .join(Item, Item.id == Listing.item_id)
        .where(
            Listing.market_id.in_(await comparable_market_ids(session, market)),
            Listing.observed_at >= since,
            Listing.kind != "bid",
        )
    )
    quotes = []
    for listing, name in (await session.execute(query)).all():
        rune = normalize_currency(name)
        price = fg_price(listing) if rune in RUNES else None
        if price:
            quotes.append(FgQuote(rune, price, listing.observed_at))
    return quotes


def rune_item_names() -> list[str]:
    """How a rune can be named in the catalog: "ber" (diablo2.io) or "ber rune"."""
    return [*RUNES, *(f"{rune} rune" for rune in RUNES)]


async def fg_markets(session: AsyncSession, now: datetime) -> list[tuple[int, MarketKey]]:
    """Markets with a recent rune price in fg: the ones a rate can be measured for."""
    query = (
        select(Market)
        .join(Listing, Listing.market_id == Market.id)
        .join(Item, Item.id == Listing.item_id)
        .where(func.lower(Item.name).in_(rune_item_names()))
        .distinct()
        .order_by(Market.id)
    )
    since = now - timedelta(days=HISTORY_DAYS)
    found = []
    for market in (await session.execute(query)).scalars():
        key = market_key(market)
        if await fg_quotes(session, key, since):
            found.append((market.id, key))
    return found


async def valuation_for(
    session: AsyncSession,
    market_id: int | None,
    market: MarketKey,
    seasons: list[Season],
    now: datetime,
) -> Valuation:
    """A market's rates, with fg measured over time unless the operator pinned it."""
    rates = await load_rates(session, market_id)
    if "fg" in await manual_currencies(session):
        return Valuation(rates)
    quotes = await fg_quotes(session, market, now - timedelta(days=HISTORY_DAYS))
    breaks = reset_days(seasons, market.ladder)
    history = fg_history(quotes, rates, until=now.date(), breaks=breaks)
    valuation = Valuation(rates, history)
    current = valuation.current_fg
    return Valuation({**rates, "fg": current.ist_per_fg}, history) if current else valuation
