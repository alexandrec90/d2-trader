"""A fingerprint of everything the pages show, so an open page knows when to redraw."""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.models import (
    AlertRule,
    CurrencyRate,
    Listing,
    Notification,
    SeasonRow,
    SourceRun,
    Watchlist,
    WatchItem,
)

# Each one moves when what a page shows moves: a job finishing, a price arriving, a rule
# firing, a rate or a date changing, or a list being edited in another tab.
SIGNALS = (
    func.max(SourceRun.id),
    func.max(Notification.id),
    func.max(Listing.last_seen_at),
    func.count(Listing.id),
    func.max(AlertRule.last_fired_at),
    func.count(AlertRule.id),
    func.count(WatchItem.id),
    func.count(Watchlist.id),
    func.max(SeasonRow.updated_at),
    func.max(CurrencyRate.updated_at),
    func.count(CurrencyRate.id),
)


async def data_version(session: AsyncSession) -> str:
    """Changes whenever anything a page could show has changed, and not otherwise."""
    values = [await session.scalar(select(signal)) for signal in SIGNALS]
    unread = await session.scalar(
        select(func.count(Notification.id)).where(Notification.read_at.is_(None))
    )
    text = "|".join(str(value) for value in [*values, unread])
    return hashlib.sha256(text.encode()).hexdigest()[:16]
