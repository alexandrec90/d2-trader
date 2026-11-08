"""Hand-pasted prices, from the Import page into `listings`."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.markets import MarketKey
from d2_trader.models import Item
from d2_trader.services.catalog import get_item_by_name
from d2_trader.services.pricing import store_listings
from d2_trader.sources import paste
from d2_trader.sources.types import ParsedListing


@dataclass(frozen=True)
class PasteRequest:
    item_id: int
    market: MarketKey
    kind: str
    ethereal: bool | None
    text: str


@dataclass(frozen=True)
class ImportResult:
    stored: int
    skipped: list[str]


def pasted_listing(
    price: paste.PastedPrice, scope: str, request: PasteRequest, now: datetime, quantity: int = 1
) -> ParsedListing:
    return ParsedListing(
        source=paste.SOURCE,
        external_id=price.external_id(scope),
        kind=request.kind,
        market=request.market,
        alternatives=price.alternatives,
        observed_at=now,
        quantity=quantity,
        ethereal=request.ethereal,
        attrs={"line": price.line},
    )


def paste_scope(request: PasteRequest, now: datetime) -> str:
    """What makes a line unique: the same line pasted again the same day counts once."""
    return f"{request.item_id}|{request.market}|{request.kind}|{request.ethereal}|{now:%Y-%m-%d}"


async def import_paste(session: AsyncSession, request: PasteRequest, now: datetime) -> ImportResult:
    """Store each priced line for the one item the request names."""
    prices, skipped = paste.parse_paste(request.text)
    scope = paste_scope(request, now)
    listings = [pasted_listing(price, scope, request, now) for price in prices]
    return ImportResult(await store_listings(session, request.item_id, listings, now), skipped)


async def rune_item(session: AsyncSession, rune: str) -> Item | None:
    """diablo2.io's catalog calls a rune "Ber"; another source might say "Ber Rune"."""
    return await get_item_by_name(session, rune) or await get_item_by_name(session, f"{rune} rune")


async def import_rune_prices(
    session: AsyncSession, market: MarketKey, kind: str, text: str, now: datetime
) -> ImportResult:
    """Store a rune price list in fg, each line against the rune it names.

    These are what the forum gold rate is measured from (see `forum_gold.py`).
    """
    prices, skipped = paste.parse_rune_paste(text)
    stored = 0
    for found in prices:
        item = await rune_item(session, found.rune)
        if item is None:
            skipped.append(f"{found.price.line} (no rune named {found.rune} in the catalog)")
            continue
        request = PasteRequest(item.id, market, kind, None, found.price.line)
        listing = pasted_listing(
            found.price, paste_scope(request, now), request, now, found.quantity
        )
        stored += await store_listings(session, item.id, [listing], now)
    return ImportResult(stored, skipped)
