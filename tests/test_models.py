from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from d2_trader.models import CurrencyRate, Market, SeasonRow, utcnow


def test_utcnow_is_timezone_aware():
    assert utcnow().utcoffset() == timedelta(0)


async def test_two_rows_cannot_describe_one_market(session):
    fields = {"platform": "PC", "mode": "softcore", "ladder": True, "game_version": "rotw"}
    session.add_all([Market(**fields), Market(**fields)])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_global_rate_is_unique_even_though_its_market_is_null(session):
    """NULLS NOT DISTINCT: without it, Postgres would keep both of these."""
    fields = {"market_id": None, "currency": "fg", "value_ist": 0.1, "source": "manual"}
    session.add_all([CurrencyRate(**fields), CurrencyRate(**fields)])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_season_number_is_its_identity(session):
    session.add(SeasonRow(number=99, starts_at=utcnow(), start_source="seed"))
    await session.flush()
    assert (await session.get(SeasonRow, 99)).number == 99
