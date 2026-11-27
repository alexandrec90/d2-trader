"""Which listing pages the operator has opened, so the offer tables can mark them."""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.models import Listing, ListingVisit


async def record_visit(session: AsyncSession, url: str, now: datetime) -> None:
    """Note that `url` was opened at `now`; opening it again moves the time forward."""
    statement = insert(ListingVisit).values(url=url, visited_at=now)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[ListingVisit.url], set_={"visited_at": statement.excluded.visited_at}
        )
    )


async def visited_urls(session: AsyncSession, urls: Iterable[str | None]) -> set[str]:
    """Those of `urls` that have been opened. A listing with no page is never visited."""
    wanted = {u for u in urls if u}
    if not wanted:
        return set()
    query = select(ListingVisit.url).where(ListingVisit.url.in_(wanted))
    return set((await session.scalars(query)).all())


async def open_listing(session: AsyncSession, listing_id: int, now: datetime) -> str | None:
    """The page of stored listing `listing_id`, recorded as visited; None if it has none."""
    listing = await session.get(Listing, listing_id)
    if listing is None or not listing.url:
        return None
    await record_visit(session, listing.url, now)
    return listing.url
