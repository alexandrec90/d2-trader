from datetime import timedelta

import pytest
import pytest_asyncio
from conftest import NOW, S15, S16

from d2_trader.markets import MarketKey
from d2_trader.models import Item, Listing
from d2_trader.services.catalog import get_or_create_market
from d2_trader.services.fg_rates import (
    fg_markets,
    fg_price,
    fg_quotes,
    rune_item_names,
    valuation_for,
)
from d2_trader.services.pricing import store_listings
from d2_trader.services.rates import set_manual_rate, store_derived_rates


@pytest_asyncio.fixture
async def runes(session):
    rows = {name: Item(name=name, category="misc") for name in ("Ber", "Jah", "Ist")}
    session.add_all(rows.values())
    await session.flush()
    return rows


async def fg_prices(session, runes, make_listing, prices, at=NOW, **fields):
    for rune, fg in prices.items():
        listing = make_listing(
            f"{rune}-{fg}-{at:%j}", fg, currency="fg", source="d2jsp", observed_at=at, **fields
        )
        await store_listings(session, runes[rune].id, [listing], at)


def test_fg_price_is_per_rune_and_needs_a_plain_fg_alternative():
    assert fg_price(Listing(alternatives=[[["fg", 300.0]]], quantity=2)) == 150.0
    assert fg_price(Listing(alternatives=[[["ber", 1.0]], [["fg", 90.0]]], quantity=1)) == 90.0
    assert fg_price(Listing(alternatives=[[["fg", 90.0], ["ist", 1.0]]], quantity=1)) is None


def test_runes_can_be_named_either_way_in_the_catalog():
    names = rune_item_names()
    assert "ber" in names
    assert "ber rune" in names


async def test_quotes_come_only_from_runes(session, runes, item, make_listing):
    await fg_prices(session, runes, make_listing, {"Ber": 150})
    shako = make_listing("shako", 400, currency="fg", source="d2jsp")
    await store_listings(session, item.id, [shako], NOW)
    [quote] = await fg_quotes(session, MarketKey(), NOW - timedelta(days=1))
    assert (quote.rune, quote.fg) == ("ber", 150)


async def test_an_offer_to_buy_is_not_a_price(session, runes, make_listing):
    await fg_prices(session, runes, make_listing, {"Ber": 150}, kind="bid")
    assert await fg_quotes(session, MarketKey(), NOW - timedelta(days=1)) == []


async def test_markets_with_rune_prices_are_found(session, runes, make_listing):
    await fg_prices(session, runes, make_listing, {"Ber": 150})
    other = MarketKey(mode="hardcore")
    await fg_prices(session, runes, make_listing, {"Jah": 200}, market=other)
    found = {key for _, key in await fg_markets(session, NOW)}
    assert found == {MarketKey(), other}


async def test_the_rate_is_measured_against_the_markets_own_rune_values(
    session, runes, make_listing
):
    market = MarketKey()
    market_id = (await get_or_create_market(session, market)).id
    await store_derived_rates(session, market_id, {"ber": 15.0, "jah": 20.0}, {"ber": 9, "jah": 9})
    await fg_prices(session, runes, make_listing, {"Ber": 150, "Jah": 200, "Ist": 10})
    valuation = await valuation_for(session, market_id, market, [S15, S16], NOW)
    # Ber 15/150, Jah 20/200, Ist 1/10: all say 1 Ist = 10 fg.
    assert valuation.rates["fg"] == pytest.approx(0.1)
    assert valuation.current_fg.low < 0.1 < valuation.current_fg.high


async def test_a_pinned_rate_is_not_measured_over(session, runes, make_listing):
    await fg_prices(session, runes, make_listing, {"Ber": 150})
    await set_manual_rate(session, "fg", 0.5)
    valuation = await valuation_for(session, None, MarketKey(), [S15, S16], NOW)
    assert valuation.rates["fg"] == 0.5
    assert valuation.fg_history == {}


async def test_without_rune_prices_the_seed_stands(session):
    valuation = await valuation_for(session, None, MarketKey(), [S15, S16], NOW)
    assert valuation.current_fg is None
    assert valuation.rates["fg"] == 0.1
