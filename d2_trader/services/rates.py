"""Currency rates: seeded, then derived from trades, and always overridable by hand."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import BASE_CURRENCY, MIN_RATE_SAMPLES, SEED_RATES
from d2_trader.models import CurrencyRate


async def load_rates(session: AsyncSession, market_id: int | None) -> dict[str, float]:
    """Seeds, under the operator's overrides, under this market's own derived rates.

    A manual rate always wins: it is how a person corrects a rate no trade can measure.
    """
    rows = (await session.execute(select(CurrencyRate))).scalars().all()
    rates = dict(SEED_RATES)
    for row in rows:
        if row.market_id == market_id and row.market_id is not None:
            rates[row.currency] = row.value_ist
    for row in rows:
        if row.source == "manual" and row.market_id in (None, market_id):
            rates[row.currency] = row.value_ist
    return rates


async def store_derived_rates(
    session: AsyncSession,
    market_id: int,
    rates: Mapping[str, float],
    samples: Mapping[str, int],
) -> int:
    """Save what trades measured for a market. Thin runes keep whatever they had."""
    stored = 0
    for currency, count in samples.items():
        if currency == BASE_CURRENCY or count < MIN_RATE_SAMPLES:
            continue
        statement = insert(CurrencyRate).values(
            market_id=market_id,
            currency=currency,
            value_ist=rates[currency],
            source="derived",
            samples=count,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[CurrencyRate.market_id, CurrencyRate.currency],
                set_={"value_ist": rates[currency], "samples": count, "source": "derived"},
                where=CurrencyRate.source != "manual",
            )
        )
        stored += 1
    return stored


async def set_manual_rate(session: AsyncSession, currency: str, value_ist: float) -> None:
    """An operator's rate for every market."""
    statement = insert(CurrencyRate).values(
        market_id=None, currency=currency, value_ist=value_ist, source="manual"
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[CurrencyRate.market_id, CurrencyRate.currency],
            set_={"value_ist": value_ist, "source": "manual"},
        )
    )


async def clear_manual_rate(session: AsyncSession, currency: str) -> None:
    query = select(CurrencyRate).where(
        CurrencyRate.currency == currency, CurrencyRate.source == "manual"
    )
    for row in (await session.execute(query)).scalars():
        await session.delete(row)


async def rate_rows(session: AsyncSession) -> list[CurrencyRate]:
    query = select(CurrencyRate).order_by(CurrencyRate.currency, CurrencyRate.market_id)
    return list((await session.execute(query)).scalars())


async def manual_currencies(session: AsyncSession) -> set[str]:
    """Currencies the operator has pinned by hand, which nothing measured may move."""
    query = select(CurrencyRate.currency).where(CurrencyRate.source == "manual")
    return set((await session.execute(query)).scalars())
