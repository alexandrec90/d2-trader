from d2_trader.currency import SEED_RATES
from d2_trader.markets import MarketKey
from d2_trader.services.catalog import get_or_create_market
from d2_trader.services.rates import (
    clear_manual_rate,
    load_rates,
    manual_currencies,
    rate_rows,
    set_manual_rate,
    store_derived_rates,
)


async def test_seeds_apply_with_nothing_stored(session):
    assert await load_rates(session, None) == SEED_RATES


async def test_a_derived_rate_belongs_to_its_market(session):
    mine = await get_or_create_market(session, MarketKey())
    other = await get_or_create_market(session, MarketKey(mode="hardcore"))
    await store_derived_rates(session, mine.id, {"ber": 30.0}, {"ber": 9})
    assert (await load_rates(session, mine.id))["ber"] == 30.0
    assert (await load_rates(session, other.id))["ber"] == SEED_RATES["ber"]


async def test_a_thin_rate_is_not_stored(session):
    mine = await get_or_create_market(session, MarketKey())
    assert await store_derived_rates(session, mine.id, {"ber": 30.0}, {"ber": 2}) == 0


async def test_ist_is_never_stored(session):
    mine = await get_or_create_market(session, MarketKey())
    assert await store_derived_rates(session, mine.id, {"ist": 3.0}, {"ist": 9}) == 0


async def test_measuring_again_replaces_the_rate(session):
    market_id = (await get_or_create_market(session, MarketKey())).id
    await store_derived_rates(session, market_id, {"ber": 30.0}, {"ber": 9})
    await store_derived_rates(session, market_id, {"ber": 25.0}, {"ber": 9})
    session.expire_all()
    assert (await load_rates(session, market_id))["ber"] == 25.0


async def test_a_manual_rate_beats_measurements_everywhere(session):
    mine = await get_or_create_market(session, MarketKey())
    await store_derived_rates(session, mine.id, {"ber": 30.0}, {"ber": 9})
    await set_manual_rate(session, "ber", 11.0)
    assert (await load_rates(session, mine.id))["ber"] == 11.0
    assert (await load_rates(session, None))["ber"] == 11.0


async def test_setting_a_manual_rate_twice_keeps_one_row(session):
    await set_manual_rate(session, "fg", 0.2)
    await set_manual_rate(session, "fg", 0.3)
    session.expire_all()
    rows = [r for r in await rate_rows(session) if r.currency == "fg"]
    assert [r.value_ist for r in rows] == [0.3]


async def test_clearing_a_manual_rate_restores_the_seed(session):
    await set_manual_rate(session, "fg", 0.2)
    await clear_manual_rate(session, "fg")
    await session.flush()
    assert (await load_rates(session, None))["fg"] == SEED_RATES["fg"]


async def test_only_hand_set_currencies_count_as_pinned(session):
    mine = await get_or_create_market(session, MarketKey())
    await store_derived_rates(session, mine.id, {"ber": 30.0}, {"ber": 9})
    await set_manual_rate(session, "fg", 0.2)
    assert await manual_currencies(session) == {"fg"}


async def test_a_new_currency_can_be_added_by_hand(session):
    await set_manual_rate(session, "annihilus", 60.0)
    assert (await load_rates(session, None))["annihilus"] == 60.0
