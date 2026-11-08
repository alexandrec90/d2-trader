"""Items and markets: what can be watched."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.markets import UNKNOWN_VERSION, MarketKey
from d2_trader.models import Item, Market
from d2_trader.sources.diablo2io import CatalogEntry


def market_key(row: Market) -> MarketKey:
    return MarketKey(row.platform, row.mode, row.ladder, row.game_version)


async def get_or_create_market(session: AsyncSession, key: MarketKey) -> Market:
    values = {
        "platform": key.platform,
        "mode": key.mode,
        "ladder": key.ladder,
        "game_version": key.game_version,
    }
    await session.execute(insert(Market).values(**values).on_conflict_do_nothing())
    return (await session.execute(select(Market).filter_by(**values))).scalar_one()


async def comparable_market_ids(session: AsyncSession, key: MarketKey) -> list[int]:
    """A market's own id, plus the same market with its game version unstated.

    diablo2.io does not say which version a sale was on, so its rows are counted here.
    """
    query = select(Market.id).where(
        Market.platform == key.platform,
        Market.mode == key.mode,
        Market.ladder == key.ladder,
        Market.game_version.in_((key.game_version, UNKNOWN_VERSION)),
    )
    return list((await session.execute(query)).scalars())


async def upsert_catalog(session: AsyncSession, entries: list[CatalogEntry]) -> int:
    """Add or refresh catalog items. Rows are kept in name order to keep runs repeatable."""
    unique = {e.name: e for e in entries}
    for entry in sorted(unique.values(), key=lambda e: e.name):
        statement = insert(Item).values(
            name=entry.name, category=entry.category, diablo2io_path=entry.path
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[Item.name],
                set_={"category": entry.category, "diablo2io_path": entry.path},
            )
        )
    return len(unique)


async def search_items(session: AsyncSession, text: str, limit: int = 15) -> list[Item]:
    """Prefix matches first, because that is what someone typing a name means."""
    needle = text.strip().replace("%", "").replace("_", "")
    if not needle:
        return []
    query = select(Item).where(Item.name.ilike(f"%{needle}%")).order_by(Item.name)
    items = list((await session.execute(query)).scalars())
    items.sort(key=lambda i: (not i.name.lower().startswith(needle.lower()), i.name))
    return items[:limit]


async def get_item_by_name(session: AsyncSession, name: str) -> Item | None:
    query = select(Item).where(func.lower(Item.name) == name.strip().lower())
    return (await session.execute(query)).scalars().first()
