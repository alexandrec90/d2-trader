"""Reading the market: from stored listings to a summary or a chart."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import unit_value
from d2_trader.forum_gold import Valuation
from d2_trader.markets import MarketKey
from d2_trader.models import Listing
from d2_trader.seasons import (
    PriceWindow,
    Season,
    SeasonState,
    reset_days,
    season_floor,
    seasonal_note,
    window_for,
)
from d2_trader.services.catalog import comparable_market_ids, get_or_create_market
from d2_trader.sources.types import ParsedListing
from d2_trader.stats import Quote, Summary, summarize
from d2_trader.trend import PathPoint, fit_path

# Sources whose asks expire when a collector stops confirming them. A pasted ask has no
# collector behind it, so it lasts as long as the window does.
CONFIRMED_SOURCES = ("traderie",)
# How far back to look for a still-open listing when no reset bounds the market.
OPEN_LISTING_DAYS = 120


@dataclass(frozen=True)
class PriceQuery:
    """One item in one market, optionally only its ethereal or non-ethereal copies."""

    item_id: int
    market: MarketKey
    ethereal: bool | None = None


@dataclass(frozen=True)
class MarketView:
    summary: Summary
    window: PriceWindow
    note: str
    quotes: list[Quote]


async def store_listings(
    session: AsyncSession, item_id: int, listings: list[ParsedListing], now: datetime
) -> int:
    """Insert or refresh listings. Seeing one again only moves its `last_seen_at`."""
    for listing in listings:
        market = await get_or_create_market(session, listing.market)
        statement = insert(Listing).values(
            source=listing.source,
            external_id=listing.external_id,
            kind=listing.kind,
            item_id=item_id,
            market_id=market.id,
            alternatives=[[list(lot) for lot in alt] for alt in listing.alternatives],
            quantity=listing.quantity,
            ethereal=listing.ethereal,
            url=listing.url,
            attrs=listing.attrs,
            observed_at=listing.observed_at,
            first_seen_at=now,
            last_seen_at=now,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[Listing.source, Listing.external_id, Listing.kind],
                set_={"last_seen_at": now, "observed_at": listing.observed_at},
            )
        )
    return len(listings)


async def _listings(session: AsyncSession, wanted: PriceQuery, since: datetime) -> list[Listing]:
    query = select(Listing).where(
        Listing.item_id == wanted.item_id,
        Listing.market_id.in_(await comparable_market_ids(session, wanted.market)),
        Listing.observed_at >= since,
    )
    if wanted.ethereal is not None:
        # A source that cannot say is not evidence for either kind of copy.
        query = query.where(Listing.ethereal.is_(wanted.ethereal))
    return list((await session.execute(query.order_by(Listing.observed_at))).scalars())


def to_quotes(listings: list[Listing], valuation: Valuation) -> list[Quote]:
    """Listings valued in Ist. One nothing can value is left out.

    Runes are valued at today's rates, since Ist is the unit they are measured against.
    Forum gold is valued at its rate on the listing's own day, because fg drifts.
    """
    quotes = []
    for listing in listings:
        rates = valuation.at(listing.observed_at)
        value = unit_value(listing.alternatives, rates, listing.kind, listing.quantity)
        if value is not None:
            quotes.append(Quote(listing.kind, value, listing.observed_at, listing.source))
    return quotes


def counts_now(
    listing: Listing, window_start: datetime, now: datetime, stale_after: timedelta
) -> bool:
    """Whether a listing describes today's market.

    A sale is history, so it counts while it is recent. An ask or a bid a collector just
    saw is still up, so it counts however long ago it was posted. A pasted one has nobody
    to say it is still up, so it is treated like history.
    """
    if listing.kind == "sold" or listing.source not in CONFIRMED_SOURCES:
        return listing.observed_at >= window_start
    return now - listing.last_seen_at <= stale_after


async def market_view(
    session: AsyncSession,
    wanted: PriceQuery,
    state: SeasonState,
    valuation: Valuation,
    now: datetime,
    stale_after: timedelta,
) -> MarketView:
    """Today's market for one item: only quotes from inside this season's window."""
    ladder = wanted.market.ladder
    window = window_for(state, ladder, now)
    # Nothing older than the season (or the last transfer) belongs to this market, even
    # a listing that is somehow still up.
    floor = season_floor(state, ladder) or now - timedelta(days=OPEN_LISTING_DAYS)
    rows = await _listings(session, wanted, min(floor, window.start))
    live = [row for row in rows if counts_now(row, window.start, now, stale_after)]
    quotes = to_quotes(live, valuation)
    # The estimate looks at every sale since the floor, not just the window: a quiet
    # week should widen the interval, not empty it.
    sales = to_quotes([row for row in rows if row.kind == "sold"], valuation)
    path = fit_path([(q.at.date(), q.value) for q in sales], now.date())
    summary = summarize(quotes, path[-1] if path else None)
    return MarketView(summary, window, seasonal_note(state, ladder), quotes)


def estimate_path(
    quotes: list[Quote], seasons: list[Season], ladder: bool, until: date
) -> list[PathPoint]:
    """The modelled price across seasons for the chart, free to jump at each reset."""
    sales = [(q.at.date(), q.value) for q in quotes if q.kind == "sold"]
    return fit_path(sales, until, reset_days(seasons, ladder))


async def history(
    session: AsyncSession, wanted: PriceQuery, valuation: Valuation, since: datetime
) -> list[Quote]:
    """All quotes since a date, across seasons: the chart marks the resets itself."""
    return to_quotes(await _listings(session, wanted, since), valuation)


async def recent_listings(
    session: AsyncSession, wanted: PriceQuery, since: datetime
) -> list[Listing]:
    return (await _listings(session, wanted, since))[::-1]
