"""Goods values from stored sales, on real Postgres."""

from datetime import UTC, datetime, timedelta

import pytest

from d2_trader.markets import MarketKey
from d2_trader.models import Item
from d2_trader.seasons import Season
from d2_trader.services.goods_values import fit_market_goods, goods_item
from d2_trader.services.pricing import store_listings
from d2_trader.services.rune_values import fit_market, rune_item

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
S15 = Season(15, datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC), "seed")
S16 = Season(16, datetime(2026, 11, 13, tzinfo=UTC), None, "seed")
SEASONS = [S15, S16]
DEEP = "deep worldstone shard"


async def test_a_missing_good_is_added_under_its_proper_name(session):
    item = await goods_item(session, "key of terror")
    assert (item.name, item.category) == ("Key of Terror", "misc")
    assert (await goods_item(session, "key of terror")).id == item.id


async def test_a_good_in_the_catalog_is_reused(session):
    row = Item(name="Worusk's End", category="misc")
    session.add(row)
    await session.flush()
    assert (await goods_item(session, "worusk's end")).id == row.id


async def test_a_good_is_valued_in_its_own_market_and_season(session, make_listing):
    ist, lo = await rune_item(session, "ist"), await rune_item(session, "lo")
    deep = await goods_item(session, DEEP)
    recent = {"observed_at": NOW - timedelta(days=1)}
    lo_sales = [make_listing(f"l{n}", 5.0, **recent) for n in range(5)]
    await store_listings(session, lo.id, lo_sales, NOW)
    rune_sales = [
        *(make_listing(f"d{n}", 20.0, DEEP, **recent) for n in range(5)),
        make_listing("ask", 2.0, DEEP, kind="ask", **recent),
        make_listing("hc", 2.0, DEEP, market=MarketKey(mode="hardcore"), **recent),
        make_listing("old", 2.0, DEEP, observed_at=S16.starts_at - timedelta(days=2)),
    ]
    await store_listings(session, ist.id, rune_sales, NOW)
    own = [make_listing(f"s{n}", 1.0, quantity=20, **recent) for n in range(3)]
    await store_listings(session, deep.id, own, NOW)
    market = MarketKey()
    fit = await fit_market(session, market, SEASONS, NOW)
    goods = await fit_market_goods(session, market, fit, SEASONS, NOW)
    assert goods.direct == {DEEP: 8}
    assert goods.measured()[DEEP] == pytest.approx(0.05, rel=0.02)
    assert goods.paths[DEEP][-1].day == NOW.date()


async def test_without_rune_values_goods_have_none(session, make_listing):
    ist = await rune_item(session, "ist")
    sold = make_listing("d", 20.0, DEEP, observed_at=NOW)
    await store_listings(session, ist.id, [sold], NOW)
    fit = await fit_market(session, MarketKey(), SEASONS, NOW)
    assert (await fit_market_goods(session, MarketKey(), fit, SEASONS, NOW)).paths == {}
