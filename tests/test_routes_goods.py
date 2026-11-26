"""The Currencies page."""

from datetime import date, timedelta

import pytest

from d2_trader.config import Settings
from d2_trader.currency import GOODS_BY_FAMILY, SEED_RATES
from d2_trader.deals import Estimate
from d2_trader.goods import GoodsFit, Sale, fit_goods
from d2_trader.markets import MarketKey
from d2_trader.models import utcnow
from d2_trader.rune_model import RuneFit, Trade, fit_runes
from d2_trader.services.goods_values import goods_item
from d2_trader.services.pricing import store_listings
from d2_trader.services.rune_values import rune_item
from d2_trader.web import routes_goods
from d2_trader.web.routes_goods import (
    Family,
    GoodPanel,
    check_deal,
    currencies_page,
    estimates,
    families,
)
from d2_trader.web.routes_runes import open_offers, page_market, valuation_rates

DEEP = "deep worldstone shard"
START = date(2026, 11, 14)


def fits(trades=5):
    runes = fit_runes(
        [Trade(START + timedelta(days=n), "lo", 1, (("ist", 5.0),)) for n in range(3)],
        SEED_RATES,
        until=START + timedelta(days=2),
    )
    sales = [Sale("ist", START + timedelta(days=n % 3), 1, [[[DEEP, 20.0]]]) for n in range(trades)]
    return runes, fit_goods(sales, runes.paths, until=START + timedelta(days=2))


def test_the_page_is_routed():
    bound = {(m, r.path): r.endpoint for r in routes_goods.router.routes for m in r.methods}
    assert bound == {("GET", "/currencies"): currencies_page}


def test_goods_are_grouped_by_family_and_the_untraded_named():
    _, goods = fits()
    found = families(goods, [], utcnow(), {})
    assert all(isinstance(f, Family) for f in found)
    assert [f.name for f in found] == list(GOODS_BY_FAMILY)
    [panel] = found[0].panels
    assert isinstance(panel, GoodPanel) and panel.asks is None  # No offers were passed.
    assert (panel.name, panel.direct, panel.few) == ("Deep Worldstone Shard", 5, False)
    assert panel.chart is not None and "20 per Ist" in panel.chart.data
    assert len(found[0].untraded) == len(GOODS_BY_FAMILY[found[0].name]) - 1


def test_a_thinly_traded_good_says_so():
    _, goods = fits(trades=2)
    assert families(goods, [], utcnow(), {})[0].panels[0].few


async def test_only_measured_goods_have_a_value(session):
    runes, goods = fits()
    rates = await valuation_rates(session, runes, goods)
    assert rates[DEEP] == pytest.approx(0.05, rel=0.02)
    assert "perfect skull" not in rates  # Only a seed, which is a guess.
    assert DEEP not in await valuation_rates(session, runes, fits(trades=2)[1])


async def test_a_pinned_good_keeps_the_pin_with_no_interval(session):
    runes, goods = fits()
    rates = {**await valuation_rates(session, runes, goods), "perfect skull": 0.1, DEEP: 0.2}
    found = estimates(runes, goods, rates)
    assert found["perfect skull"] == Estimate(0.1, 0.1, 0.1)
    assert found[DEEP] == Estimate(0.2, 0.2, 0.2)
    assert found["lo"].low < found["lo"].value < found["lo"].high


async def test_with_no_trades_nothing_is_estimated(session):
    empty = RuneFit({}, {}, 0)
    assert estimates(empty, GoodsFit(), await valuation_rates(session, empty)) == {}


def test_a_deal_is_checked_only_once_typed():
    assert check_deal("", " ", {}) == (None, None)
    deal, error = check_deal("20 deep shards", "", {})
    assert deal is None and "both sides" in error


def test_a_bad_market_is_replaced_part_by_part():
    assert page_market("PC", "nonsense", False, "lod") == MarketKey(
        ladder=False, game_version="lod"
    )


def test_goods_are_fetched_less_often_than_runes_unless_configured():
    settings = Settings(_env_file=None, database_url="postgresql://x")
    assert settings.goods_poll_hours == 3 > settings.rates_poll_hours


@pytest.mark.parametrize("path", ["/currencies", "/runes"])
async def test_a_page_reads_its_market_from_the_query(client, path):
    page = (await client.get(f"{path}?mode=hardcore&ladder=false&version=nonsense")).text
    # A bad part is replaced by its default, the rest kept.
    assert f"{MarketKey(mode='hardcore', ladder=False).label} · from" in page


async def test_the_empty_page_still_lists_every_family(client):
    page = (await client.get("/currencies")).text
    assert all(name in page for name in GOODS_BY_FAMILY)
    assert 'href="/currencies"' in page


async def store_market(session, make_listing, now):
    ist, lo = await rune_item(session, "ist"), await rune_item(session, "lo")
    deep = await goods_item(session, DEEP)
    lo_sales = [make_listing(f"l{n}", 5.0, observed_at=now) for n in range(5)]
    await store_listings(session, lo.id, lo_sales, now)
    sold = [make_listing(f"d{n}", 20.0, DEEP, observed_at=now) for n in range(6)]
    await store_listings(session, ist.id, sold, now)
    asks = [make_listing("rune-ask", 18.0, DEEP, kind="ask", observed_at=now)]
    await store_listings(session, ist.id, asks, now)
    good_ask = [make_listing("good-ask", 1.0, kind="ask", quantity=25, observed_at=now)]
    await store_listings(session, deep.id, good_ask, now)


async def test_the_page_values_a_good_and_judges_a_deal(client, sessions, make_listing):
    now = utcnow()
    async with sessions() as session:
        await store_market(session, make_listing, now)
        await session.commit()
    page = (await client.get("/currencies?give=14+deep+shards&get=an+ist")).text
    assert "Deep Worldstone Shard" in page and "20 per Ist" in page
    assert "Good deal" in page and "Break-even is 20 Deep Worldstone Shard" in page
    assert "-20%" in page  # 25 for an Ist, against 20.


async def test_the_page_says_what_is_wrong_with_a_deal(client):
    page = (await client.get("/currencies?give=20+worldstone+shards&get=ist")).text
    assert "could be Deep Worldstone Shard" in page


async def test_a_rune_on_sale_for_a_good_is_ranked_on_the_runes_page(
    client, sessions, make_listing
):
    now = utcnow()
    async with sessions() as session:
        await store_market(session, make_listing, now)
        await session.commit()
    page = (await client.get("/runes")).text
    assert "18 Deep Worldstone Shard" in page and "-10%" in page


async def test_offers_are_open_as_of_each_kinds_own_job(session, settings, make_listing):
    now = utcnow()
    await store_market(session, make_listing, now - timedelta(hours=3))
    found = await open_offers(session, MarketKey(), settings, now)
    assert [row.external_id for _, row in found] == ["good-ask"]
