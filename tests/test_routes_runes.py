"""The rune values page."""

import re
from datetime import date, timedelta

import pytest

from d2_trader.config import Settings
from d2_trader.currency import SEED_RATES
from d2_trader.markets import MarketKey
from d2_trader.models import CurrencyRate, utcnow
from d2_trader.rune_model import RuneFit, Trade, fit_runes
from d2_trader.services.catalog import get_or_create_market
from d2_trader.services.pricing import store_listings
from d2_trader.services.rates import set_manual_rate
from d2_trader.services.rune_values import measured_markets, rune_item
from d2_trader.sources.types import ParsedListing
from d2_trader.web import routes_runes
from d2_trader.web.routes_runes import (
    CHARTED,
    RunePanel,
    hours_label,
    market_query,
    panels,
    runes_page,
    valuation_rates,
)


def test_the_page_is_routed():
    bound = {(m, r.path): r.endpoint for r in routes_runes.router.routes for m in r.methods}
    assert bound == {("GET", "/runes"): runes_page}


def test_runes_are_charted_most_valuable_first():
    assert CHARTED[0] == "zod" and CHARTED[-1] == "pul"
    assert CHARTED.index("ber") < CHARTED.index("lo") < CHARTED.index("ist")


def test_a_market_is_spelled_as_the_pages_query():
    query = market_query(MarketKey("PC", "hardcore", False, "lod"))
    assert query == "platform=PC&mode=hardcore&ladder=false&version=lod"


def test_without_a_fit_there_are_no_panels():
    assert panels(RuneFit({}, {}, 0), [], utcnow()) == []


def test_every_charted_rune_gets_a_panel_valued_in_ber_too():
    start = date(2026, 11, 14)
    trades = [Trade(start + timedelta(days=n), "ber", 1, (("ist", 14.0),)) for n in range(3)]
    fit = fit_runes(trades, SEED_RATES, until=start + timedelta(days=2))
    found = panels(fit, [], utcnow())
    assert [p.rune for p in found] == CHARTED
    assert all(isinstance(p, RunePanel) and p.asks is None for p in found)
    ber = next(p for p in found if p.rune == "ber")
    assert ber.ber == ber.today.value and ber.direct == 3 and ber.chart is not None


async def test_the_page_says_how_to_start_when_nothing_is_stored(client):
    page = (await client.get("/runes")).text
    assert "No rune trades stored" in page
    assert 'href="/runes"' in page  # It is in the navigation.


async def test_the_page_charts_stored_rune_trades(client, sessions):
    now = utcnow()
    async with sessions() as session:
        lo = await rune_item(session, "lo")
        sales = [
            ParsedListing("traderie", f"t{n}", "sold", MarketKey(), [[("ist", 6.0)]], at)
            for n, at in enumerate([now, now - timedelta(days=1), now - timedelta(days=2)])
        ]
        await store_listings(session, lo.id, sales, now)
        await session.commit()
    page = (await client.get("/runes")).text
    assert "from 3 rune-for-rune trades" in page
    assert page.count("data-chart") == len(CHARTED)
    assert "3 trades name it" in page


async def test_the_page_lists_each_runes_cheapest_open_asks(client, sessions):
    now = utcnow()
    async with sessions() as session:
        lo = await rune_item(session, "lo")
        asks = [
            ParsedListing("traderie", f"a{n}", "ask", MarketKey(), [[("ist", v)]], now)
            for n, v in enumerate([7.0, 6.5, 6.0])
        ]
        sold = [ParsedListing("traderie", "s", "sold", MarketKey(), [[("ist", 6.0)]], now)]
        await store_listings(session, lo.id, asks + sold, now)
        await session.commit()
    page = (await client.get("/runes")).text
    assert "Cheapest 3 offers" in page
    start = page.index('<table class="asks">')
    table = page[start : page.index("</table>", start)]
    assert re.findall(r"<td>([\d.]+ Ist)</td>", table) == ["6 Ist", "6.5 Ist", "7 Ist"]
    assert "+8%" in page  # 6.5 Ist against a Lo estimated at 6.
    assert page.count("No open offers seen in the last hour.") == len(CHARTED) - 1
    assert "fetched with the trades every hour," in page


async def test_the_page_lists_what_people_who_want_another_rune_pay(client, sessions):
    now = utcnow()
    bid = ParsedListing("traderie", "b", "bid", MarketKey(), [[("lo", 1.0)]], now, quantity=5)
    sold = ParsedListing("traderie", "s", "sold", MarketKey(), [[("ist", 6.0)]], now)
    async with sessions() as session:
        ist, lo = await rune_item(session, "ist"), await rune_item(session, "lo")
        await store_listings(session, ist.id, [bid], now)
        await store_listings(session, lo.id, [sold], now)
        await session.commit()
    page = (await client.get("/runes")).text
    assert "Cheapest 1 offers" in page
    assert re.search(r"<td>5 Ist for 1 Lo <span", page)
    assert "they want" in page


async def test_trade_ups_have_their_own_tab_not_a_panel_here(client, sessions):
    now = utcnow()
    listings = [
        ParsedListing("traderie", "s", "sold", MarketKey(), [[("ist", 6.0)]], now),
        ParsedListing("traderie", "up", "ask", MarketKey(), [[("pul", 15.0)]], now),
    ]
    async with sessions() as session:
        lo = await rune_item(session, "lo")
        await store_listings(session, lo.id, listings, now)
        await session.commit()
    page = (await client.get("/runes")).text
    assert "trade-ups" not in page
    assert 'href="/trade-up"' in page  # It is in the navigation.


def test_a_poll_interval_reads_as_a_span():
    assert hours_label(1) == "hour"
    assert hours_label(12) == "12 hours"


async def test_the_empty_page_states_the_configured_interval(client):
    assert "collects them every hour;" in (await client.get("/runes")).text


def test_rune_trades_and_asks_are_fetched_hourly_unless_configured():
    assert Settings(database_url="postgresql://x").rates_poll_hours == 1


async def test_asks_are_valued_at_the_model_unless_a_rate_is_pinned(session):
    start = date(2026, 11, 14)
    trades = [Trade(start + timedelta(days=n), "lo", 1, (("ist", 7.0),)) for n in range(3)]
    fit = fit_runes(trades, SEED_RATES, until=start + timedelta(days=2))
    assert (await valuation_rates(session, fit))["lo"] == pytest.approx(7, rel=0.02)
    await set_manual_rate(session, "lo", 3.0)
    await session.flush()
    assert (await valuation_rates(session, fit))["lo"] == 3.0


async def test_a_bad_market_falls_back_to_the_default(client):
    page = (await client.get("/runes?mode=nonsense&version=nope")).text
    assert MarketKey().label in page


async def test_measured_markets_put_the_default_first(session):
    hardcore = await get_or_create_market(session, MarketKey(mode="hardcore"))
    session.add(CurrencyRate(market_id=hardcore.id, currency="lo", value_ist=5, source="derived"))
    await session.flush()
    assert await measured_markets(session) == [MarketKey(), MarketKey(mode="hardcore")]
