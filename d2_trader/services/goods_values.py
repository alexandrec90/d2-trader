"""Goods values per market, from the sales stored in `listings`.

Reads `listings` directly rather than through `market_view`, like `rune_values.py` and for
its reason: it needs every item at once. It reads from the same floor, so a ladder price
never crosses a season boundary.
"""

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import GOODS, normalize_currency
from d2_trader.goods import GoodsFit, Sale, fit_goods
from d2_trader.markets import MarketKey
from d2_trader.models import Item
from d2_trader.rune_model import RuneFit
from d2_trader.seasons import Season, reset_days
from d2_trader.services.catalog import get_item_by_name
from d2_trader.services.fg_rates import rune_item_names
from d2_trader.services.rune_values import model_floor, sold_rows


async def goods_item(session: AsyncSession, good: str) -> Item:
    """The catalog's row for a good, made if the catalog lacks it."""
    item = await get_item_by_name(session, good)
    if item is None:
        item = Item(name=GOODS[good], category="misc")
        session.add(item)
        await session.flush()
    return item


async def fit_market_goods(
    session: AsyncSession, market: MarketKey, fit: RuneFit, seasons: list[Season], now: datetime
) -> GoodsFit:
    """Every good's value in one market, day by day, against that market's rune values."""
    floor = model_floor(seasons, market.ladder, now)
    rows = await sold_rows(session, market, floor, [*rune_item_names(), *GOODS])
    sales = [
        Sale(normalize_currency(name), row.observed_at.date(), row.quantity, row.alternatives)
        for name, row in rows
    ]
    breaks = reset_days(seasons, market.ladder)
    # Seconds of arithmetic late in a season, which would hold up every other page.
    return await asyncio.to_thread(fit_goods, sales, fit.paths, now.date(), breaks)
