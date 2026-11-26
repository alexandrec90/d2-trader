"""What the operator holds in each market, kept so the Trade up page need not ask again.

A ladder stash and a non-ladder one are different stashes, so each market has its own.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import GOODS, GOODS_BY_FAMILY, RUNES, display_name
from d2_trader.markets import MarketKey
from d2_trader.models import Holding, Market
from d2_trader.services.catalog import get_or_create_market

# A form field per currency: `held:pul`, `held:perfect amethyst`.
FIELD = "held:"
# The runes that are traded for themselves; the ones below Pul are pocket change and
# sit folded away, though they can still pay.
TRADED_FROM = RUNES.index("pul")
HOLDABLE = frozenset((*RUNES, *GOODS))


class BadHolding(ValueError):
    """A count on the form that is not a whole number of a known currency."""


@dataclass(frozen=True)
class HoldingGroup:
    """One block of the form: a title, and each currency in it with how many are held."""

    title: str
    rows: list[tuple[str, str, int]]  # Currency key, its name, how many.
    folded: bool  # Shown closed, because none of it is held.


def read_quantities(fields: Iterable[tuple[str, str]]) -> dict[str, int]:
    """The form's `held:` fields as counts. A blank or zero holds none.

    Raises `BadHolding` for a negative or fractional count or an unknown currency, so a
    typo never quietly saves as nothing.
    """
    held: dict[str, int] = {}
    for name, raw in fields:
        if not name.startswith(FIELD):
            continue
        currency = name.removeprefix(FIELD)
        if currency not in HOLDABLE:
            raise BadHolding(f"No currency called '{currency}'")
        text = raw.strip()
        if not text:
            continue
        try:
            count = int(text)
        except ValueError:
            raise BadHolding(f"{display_name(currency)}: '{text}' is not a whole number") from None
        if count < 0:
            raise BadHolding(f"{display_name(currency)}: a count cannot be below zero")
        if count:
            held[currency] = held.get(currency, 0) + count
    return held


def holding_groups(held: Mapping[str, int]) -> list[HoldingGroup]:
    """The form's blocks: the traded runes open, low runes and each goods family folded
    unless something in them is held."""
    blocks = [
        ("Runes", RUNES[TRADED_FROM:], False),
        ("Low runes", RUNES[:TRADED_FROM], True),
        *((family, [n.lower() for n in names], True) for family, names in GOODS_BY_FAMILY.items()),
    ]
    groups = []
    for title, currencies, foldable in blocks:
        rows = [(c, display_name(c), held.get(c, 0)) for c in currencies]
        folded = foldable and not any(count for _, _, count in rows)
        groups.append(HoldingGroup(title, rows, folded))
    return groups


async def load_holdings(session: AsyncSession, market: MarketKey) -> dict[str, int]:
    """What is held in one market; nothing if none was ever saved."""
    query = (
        select(Holding.currency, Holding.quantity)
        .join(Market, Market.id == Holding.market_id)
        .where(
            Market.platform == market.platform,
            Market.mode == market.mode,
            Market.ladder == market.ladder,
            Market.game_version == market.game_version,
        )
    )
    return {currency: count for currency, count in (await session.execute(query)).all()}


async def save_holdings(session: AsyncSession, market: MarketKey, held: Mapping[str, int]) -> None:
    """Replace what is held in one market with `held`; a count of zero holds none."""
    row = await get_or_create_market(session, market)
    await session.execute(delete(Holding).where(Holding.market_id == row.id))
    session.add_all(
        Holding(market_id=row.id, currency=c, quantity=q) for c, q in held.items() if q > 0
    )
    await session.flush()
