from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_diablo2io import BANNER, page, sale
from test_traderie import raw_listing

from d2_trader.markets import MarketKey
from d2_trader.models import Item, Listing, Notification, Watchlist
from d2_trader.services import collector
from d2_trader.services.calendar import ensure_seed_seasons, load_seasons
from d2_trader.services.collector import (
    SourceDisabled,
    Target,
    shuffled,
    watched_items,
    watched_targets,
)
from d2_trader.services.pricing import store_listings
from d2_trader.sources.types import ParsedListing
from d2_trader.services.rates import load_rates, rate_rows
from d2_trader.services.watch import add_watch, enabled_watches
from d2_trader.sources.diablo2io import CATEGORIES
from d2_trader.sources.http import SourceBlocked

NOW = datetime(2026, 11, 6, tzinfo=UTC)
NEWS_PATH = "/en-us/feed/diablo-2-resurrected"
ARTICLE_PATH = "/en-us/article/24316567/diablo-ii-resurrected-ladder-season-16"
NEWS = f'<a href="{ARTICLE_PATH}">x</a>'
ARTICLE = """
<p>Ladder Season 15 Ends: Monday, November 9, 11 a.m. PST</p>
<p>Ladder Season 16 Starts: Friday, November 13, 5 p.m. PST</p>
"""


def transport(pages, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        status, body = pages.get(request.url.path, (404, ""))
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


@pytest_asyncio.fixture
async def watch(session, item, market):
    session.add(Watchlist(name="L"))
    await session.flush()
    watchlist = (await session.execute(select(Watchlist))).scalars().first()
    return await add_watch(session, watchlist.id, item.id, market, None)


async def count(session, model=Listing):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_one_item_on_two_lists_is_collected_once(session, watch, item, market):
    other = Watchlist(name="M")
    session.add(other)
    await session.flush()
    await add_watch(session, other.id, item.id, market, None)
    watches = await enabled_watches(session)
    assert len(watched_items(watches)) == 1
    assert watched_targets(watches) == [Target(watch.item, market)]


async def test_one_item_on_two_markets_is_two_targets(session, watch, item):
    await add_watch(session, watch.watchlist_id, item.id, MarketKey(ladder=False), None)
    assert len(watched_targets(await enabled_watches(session))) == 2


async def test_catalog_sync_fills_items(session, settings):
    index = '<h3 class="z-sort-name"><a href="/misc/ber-t1.html">Ber</a></h3>'
    pages = {f"/{c}/": (200, "") for c in CATEGORIES} | {"/misc/": (200, index)}
    assert await collector.sync_catalog(session, settings, NOW, transport(pages)) == 1
    assert await count(session, Item) == 1


async def test_diablo2io_stores_sales_for_watched_items(session, settings, watch, item):
    html = page(sale([(1, "Ist")], ["Ladder", "PC"]))
    pages = {item.diablo2io_path: (200, html)}
    assert await collector.collect_diablo2io(session, settings, NOW, transport(pages)) == 1
    assert await count(session) == 1


async def test_collecting_again_adds_no_duplicates(session, settings, watch, item):
    html = page(sale([(1, "Ist")], ["Ladder", "PC"]))
    pages = transport({item.diablo2io_path: (200, html)})
    await collector.collect_diablo2io(session, settings, NOW, pages)
    await collector.collect_diablo2io(session, settings, NOW, pages)
    assert await count(session) == 1


async def test_a_switched_off_source_makes_no_requests(session, settings, watch):
    settings.diablo2io_enabled = False
    seen = []
    with pytest.raises(SourceDisabled):
        await collector.collect_diablo2io(session, settings, NOW, transport({}, seen))
    assert seen == []


async def test_traderie_makes_no_requests_unless_enabled(session, settings, watch):
    """The promise the Traderie decision rests on, and it must never quietly regress."""
    seen = []
    with pytest.raises(SourceDisabled, match="TRADERIE_ENABLED"):
        await collector.collect_traderie(session, settings, NOW, transport({}, seen))
    with pytest.raises(SourceDisabled):
        await collector.refresh_rates(session, settings, NOW, transport({}, seen))
    assert seen == []


def traderie_api(listing=None):
    return {
        "/api/diablo2resurrected/items": (200, {"items": [{"id": "77", "name": "Test Crest"}]}),
        "/api/diablo2resurrected/listings": (200, {"listings": [listing or raw_listing()]}),
    }


async def test_enabled_traderie_collects_every_kind(session, settings, watch, item):
    settings.traderie_enabled = True
    seen = []
    await collector.collect_traderie(session, settings, NOW, transport(traderie_api(), seen))
    assert item.traderie_id == "77"
    assert await count(session) == 3  # The same fake listing as an ask, a bid and a sale.
    calls = [r for r in seen if r.url.path.endswith("/listings")]
    query = parse_qs(calls[0].url.query.decode())
    assert query["prop_Mode"] == ["softcore"]
    assert query["prop_Ladder"] == ["true"]


async def test_a_resolved_traderie_id_is_not_searched_again(session, settings, watch, item):
    settings.traderie_enabled = True
    item.traderie_id = "77"
    seen = []
    await collector.collect_traderie(session, settings, NOW, transport(traderie_api(), seen))
    assert not [r for r in seen if r.url.path.endswith("/items")]


async def test_an_item_traderie_does_not_list_is_skipped(session, settings, watch):
    settings.traderie_enabled = True
    pages = {"/api/diablo2resurrected/items": (200, {"items": []})}
    assert await collector.collect_traderie(session, settings, NOW, transport(pages)) == 0


async def test_a_blocked_traderie_stops_the_run(session, settings, watch):
    settings.traderie_enabled = True
    pages = {"/api/diablo2resurrected/items": (402, "")}
    with pytest.raises(SourceBlocked):
        await collector.collect_traderie(session, settings, NOW, transport(pages))


async def test_rates_are_measured_per_watched_market(session, settings, watch):
    settings.traderie_enabled = True
    sold_for_jah = raw_listing(prices=[{"name": "Ist Rune", "quantity": 40, "group": 0}])
    pages = {"/api/diablo2resurrected/listings": (200, {"listings": [sold_for_jah] * 6})}
    await collector.refresh_rates(session, settings, NOW, transport(pages))
    assert (await load_rates(session, watch.market_id))["jah"] == 40.0
    assert await rate_rows(session)


async def test_rates_are_also_measured_where_fg_prices_need_them(session, settings):
    """No watch, but rune prices in fg: the fg rate needs that market's rune values."""
    settings.traderie_enabled = True
    ber = Item(name="Ber", category="misc")
    session.add(ber)
    await session.flush()
    hardcore = MarketKey(mode="hardcore")
    listing = ParsedListing("d2jsp", "1", "ask", hardcore, [[("fg", 150.0)]], NOW, quantity=1)
    await store_listings(session, ber.id, [listing], NOW)
    seen = []
    pages = {"/api/diablo2resurrected/listings": (200, {"listings": []})}
    await collector.refresh_rates(session, settings, NOW, transport(pages, seen))
    calls = [parse_qs(r.url.query.decode()) for r in seen if r.url.path.endswith("/listings")]
    assert {c["prop_Mode"][0] for c in calls} == {"hardcore"}


def test_a_shuffled_list_has_the_same_things_in_it():
    things = list(range(20))
    assert sorted(shuffled(things)) == things
    assert things == list(range(20))  # Untouched.


async def test_season_sync_records_official_dates_and_says_so(session, settings):
    await ensure_seed_seasons(session)
    pages = {
        NEWS_PATH: (200, NEWS),
        ARTICLE_PATH: (200, ARTICLE),
        "/": (200, BANNER),
    }
    assert await collector.sync_seasons(session, settings, NOW, transport(pages)) == 2
    seasons = {s.number: s for s in await load_seasons(session)}
    assert seasons[15].end_source == "blizzard"
    assert seasons[16].start_source == "blizzard"
    assert seasons[16].url.endswith("ladder-season-16")
    titles = set((await session.execute(select(Notification.title))).scalars())
    assert "Season 15 end date set" in titles
    assert "Season 16 start date set" in titles


async def test_season_sync_fails_loudly_when_blizzards_format_moves(session, settings):
    await ensure_seed_seasons(session)
    pages = {NEWS_PATH: (200, "<html>redesigned</html>"), "/": (200, BANNER)}
    with pytest.raises(ValueError, match="no schedule"):
        await collector.sync_seasons(session, settings, NOW, transport(pages))


async def test_a_failed_cross_check_does_not_cost_the_official_dates(session, settings):
    await ensure_seed_seasons(session)
    pages = {NEWS_PATH: (200, NEWS), ARTICLE_PATH: (200, ARTICLE), "/": (403, "")}
    assert await collector.sync_seasons(session, settings, NOW, transport(pages)) == 2


async def test_conflicting_sources_raise_a_notification(session, settings):
    await ensure_seed_seasons(session)
    wrong = BANNER.replace("14th November", "21st November")
    pages = {NEWS_PATH: (200, NEWS), ARTICLE_PATH: (200, ARTICLE), "/": (200, wrong)}
    await collector.sync_seasons(session, settings, NOW, transport(pages))
    titles = set((await session.execute(select(Notification.title))).scalars())
    assert "Season 16 dates disagree" in titles
    seasons = {s.number: s for s in await load_seasons(session)}
    assert seasons[16].starts_at == datetime(2026, 11, 14, 1, tzinfo=UTC)


async def test_a_season_milestone_is_sent_once(session, settings):
    await ensure_seed_seasons(session)
    soon = datetime(2026, 11, 8, tzinfo=UTC)
    assert await collector.send_season_events(session, settings, soon) == 1
    assert await collector.send_season_events(session, settings, soon) == 0
