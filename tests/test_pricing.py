from datetime import UTC, datetime, timedelta

import pytest
from conftest import NOW, S15, S16
from sqlalchemy import func, select

from d2_trader.currency import SEED_RATES
from d2_trader.forum_gold import FgEstimate, Valuation
from d2_trader.markets import UNKNOWN_VERSION, MarketKey
from d2_trader.models import Listing
from d2_trader.seasons import season_state
from d2_trader.stats import Quote
from d2_trader.services.pricing import (
    MarketView,
    PriceQuery,
    history,
    counts_now,
    estimate_path,
    market_view,
    recent_listings,
    store_listings,
    to_quotes,
)

STALE = timedelta(hours=6)


async def view(session, item, market, state, now=NOW, ethereal=None) -> MarketView:
    wanted = PriceQuery(item.id, market, ethereal)
    return await market_view(session, wanted, state, Valuation(dict(SEED_RATES)), now, STALE)


async def test_storing_twice_keeps_one_row_and_refreshes_last_seen(session, item, make_listing):
    listing = make_listing("a", 3)
    later = NOW + timedelta(hours=1)
    await store_listings(session, item.id, [listing], NOW)
    await store_listings(session, item.id, [listing], later)
    session.expire_all()
    count = await session.execute(select(func.count()).select_from(Listing))
    assert count.scalar_one() == 1
    row = (await session.execute(select(Listing))).scalar_one()
    assert (row.first_seen_at, row.last_seen_at) == (NOW, later)


async def test_the_same_id_as_ask_then_sold_is_two_facts(session, item, make_listing):
    await store_listings(
        session,
        item.id,
        [make_listing("a", 3, kind="ask"), make_listing("a", 3, kind="sold")],
        NOW,
    )
    count = await session.execute(select(func.count()).select_from(Listing))
    assert count.scalar_one() == 2


async def test_view_summarizes_a_market(session, item, market, make_listing, mid_season):
    await store_listings(session, item.id, [make_listing(str(i), 10) for i in range(6)], NOW)
    result = await view(session, item, market, mid_season)
    assert result.summary.fair.median == 10
    assert result.summary.basis == "sold"


async def test_markets_do_not_leak_into_each_other(session, item, market, make_listing, mid_season):
    hardcore = MarketKey(mode="hardcore")
    non_ladder = MarketKey(ladder=False)
    await store_listings(
        session,
        item.id,
        [
            make_listing("hc", 100, market=hardcore),
            make_listing("nl", 200, market=non_ladder),
            make_listing("mine", 10),
        ],
        NOW,
    )
    assert (await view(session, item, market, mid_season)).summary.sold_count == 1


async def test_a_sale_of_unstated_version_counts_towards_the_market(
    session, item, market, make_listing, mid_season
):
    unstated = MarketKey(game_version=UNKNOWN_VERSION)
    await store_listings(session, item.id, [make_listing("d2io", 10, market=unstated)], NOW)
    assert (await view(session, item, market, mid_season)).summary.sold_count == 1


async def test_a_new_season_starts_with_no_ladder_history(session, item, market, make_listing):
    """The heart of reset handling: last season's sale says nothing about this season."""
    launch = datetime(2026, 11, 15, 12, tzinfo=UTC)
    before_reset = launch - timedelta(days=7)
    await store_listings(
        session,
        item.id,
        [make_listing(str(i), 2, observed_at=before_reset) for i in range(6)],
        launch,
    )
    result = await view(session, item, market, season_state(launch, [S15, S16]), now=launch)
    assert result.summary.fair is None


async def test_a_new_season_sees_its_own_sales(session, item, market, make_listing):
    launch = datetime(2026, 11, 15, 12, tzinfo=UTC)
    await store_listings(
        session,
        item.id,
        [make_listing(str(i), 40, observed_at=launch) for i in range(6)],
        launch,
    )
    result = await view(session, item, market, season_state(launch, [S15, S16]), now=launch)
    assert result.summary.fair.median == 40


async def test_a_ladder_market_is_closed_between_seasons(session, item, market):
    gap = datetime(2026, 11, 11, tzinfo=UTC)
    result = await view(session, item, market, season_state(gap, [S15, S16]), now=gap)
    assert result.window.closed is True
    assert "stale" in result.note


async def test_an_ask_nobody_has_confirmed_expires(session, item, market, make_listing, mid_season):
    old = NOW - timedelta(hours=7)
    await store_listings(session, item.id, [make_listing("a", 5, kind="ask")], old)
    assert (await view(session, item, market, mid_season)).summary.ask_count == 0


async def test_an_old_offer_that_is_still_up_counts(
    session, item, market, make_listing, mid_season
):
    """Regression: a buy request posted weeks ago but seen just now was left out."""
    posted = NOW - timedelta(days=30)
    listing = make_listing("bid", 5, kind="bid", observed_at=posted)
    await store_listings(session, item.id, [listing], NOW)
    assert (await view(session, item, market, mid_season)).summary.highest_bid == 5


async def test_a_listing_still_up_from_last_season_does_not_count(
    session, item, market, make_listing
):
    launch = datetime(2026, 11, 15, 12, tzinfo=UTC)
    posted = launch - timedelta(days=8)  # Season 15.
    listing = make_listing("ask", 5, kind="ask", observed_at=posted)
    await store_listings(session, item.id, [listing], launch)
    state = season_state(launch, [S15, S16])
    assert (await view(session, item, market, state, now=launch)).summary.ask_count == 0


async def test_an_old_sale_still_ages_out_of_the_window(
    session, item, market, make_listing, mid_season
):
    sold = make_listing("s", 5, observed_at=NOW - timedelta(days=30))
    await store_listings(session, item.id, [sold], NOW)
    assert (await view(session, item, market, mid_season)).summary.sold_count == 0


async def test_a_pasted_ask_has_no_collector_to_confirm_it(
    session, item, market, make_listing, mid_season
):
    old = NOW - timedelta(hours=7)
    listing = make_listing("a", 5, kind="ask", source="d2jsp", observed_at=old)
    await store_listings(session, item.id, [listing], old)
    assert (await view(session, item, market, mid_season)).summary.ask_count == 1


async def test_ethereal_filter_keeps_copies_apart(session, item, market, make_listing, mid_season):
    await store_listings(
        session,
        item.id,
        [
            make_listing("eth", 100, ethereal=True),
            make_listing("plain", 10, ethereal=False),
            make_listing("unknown", 50, ethereal=None),
        ],
        NOW,
    )
    only_eth = await view(session, item, market, mid_season, ethereal=True)
    assert only_eth.summary.fair.median == 100
    assert only_eth.summary.sold_count == 1
    assert (await view(session, item, market, mid_season)).summary.sold_count == 3


def test_a_listing_nothing_can_value_is_left_out():
    known = Listing(kind="sold", alternatives=[[["ist", 2.0]]], quantity=1, observed_at=NOW)
    unknown = Listing(kind="sold", alternatives=[[["mystery", 2.0]]], quantity=1, observed_at=NOW)
    assert [q.value for q in to_quotes([known, unknown], Valuation(SEED_RATES))] == [2.0]


def test_rates_apply_at_read_time_so_a_correction_reaches_history():
    row = Listing(kind="sold", alternatives=[[["fg", 100.0]]], quantity=1, observed_at=NOW)
    assert to_quotes([row], Valuation({"fg": 0.1}))[0].value == 10
    assert to_quotes([row], Valuation({"fg": 0.2}))[0].value == 20


def test_forum_gold_is_valued_at_the_rate_of_its_own_day():
    """A September fg price was paid in September's fg, not today's."""
    october = datetime(2026, 9, 1, tzinfo=UTC)
    rows = [
        Listing(kind="sold", alternatives=[[["fg", 100.0]]], quantity=1, observed_at=october),
        Listing(kind="sold", alternatives=[[["fg", 100.0]]], quantity=1, observed_at=NOW),
    ]
    history = {
        october.date(): FgEstimate(0.05, 0.04, 0.06, 1),
        NOW.date(): FgEstimate(0.1, 0.09, 0.11, 1),
    }
    values = [q.value for q in to_quotes(rows, Valuation({"fg": 0.1}, history))]
    assert values == [5.0, 10.0]


def test_rune_prices_ignore_the_fg_history():
    row = Listing(kind="sold", alternatives=[[["ber", 1.0]]], quantity=1, observed_at=NOW)
    history = {NOW.date(): FgEstimate(0.5, 0.4, 0.6, 1)}
    assert to_quotes([row], Valuation({"ber": 12.0}, history))[0].value == 12.0


async def test_the_estimate_uses_sales_older_than_the_window(
    session, item, market, make_listing, mid_season
):
    """A quiet week leaves an estimate with a wider band, not an empty one."""
    old = [make_listing(f"o{i}", 10, observed_at=NOW - timedelta(days=20 + i)) for i in range(6)]
    await store_listings(session, item.id, old, NOW)
    summary = (await view(session, item, market, mid_season)).summary
    assert summary.sold_count == 0  # Nothing in the 14-day window...
    assert summary.estimate.value == pytest.approx(10, rel=0.05)  # ...yet a price.
    assert summary.metric("estimate") == summary.estimate.value


async def test_the_estimate_ignores_last_seasons_sales(session, item, market, make_listing):
    launch = datetime(2026, 11, 15, 12, tzinfo=UTC)
    before = [
        make_listing(f"b{i}", 2, observed_at=launch - timedelta(days=8 + i)) for i in range(6)
    ]
    await store_listings(session, item.id, before, launch)
    state = season_state(launch, [S15, S16])
    assert (await view(session, item, market, state, now=launch)).summary.estimate is None


def test_the_chart_path_may_jump_at_a_reset():
    reset = S16.starts_at
    quotes = [Quote("sold", 2.0, reset - timedelta(days=d)) for d in range(1, 15)] + [
        Quote("sold", 20.0, reset + timedelta(days=d)) for d in range(0, 3)
    ]
    path = estimate_path(quotes, [S15, S16], ladder=True, until=(reset + timedelta(days=2)).date())
    assert path[-1].value == pytest.approx(20, rel=0.15)
    assert path[0].value == pytest.approx(2, rel=0.15)


def test_what_counts_as_the_market_now():
    start = NOW - timedelta(days=5)
    fresh, old = NOW - timedelta(hours=1), NOW - timedelta(days=9)

    def listing(kind, source, observed, seen=fresh):
        return Listing(kind=kind, source=source, observed_at=observed, last_seen_at=seen)

    # A sale counts while it is inside the window, however recently it was seen.
    assert counts_now(listing("sold", "traderie", NOW), start, NOW, STALE)
    assert not counts_now(listing("sold", "traderie", old), start, NOW, STALE)
    # A collected ask or bid counts while it is still up, however long ago it was posted.
    assert counts_now(listing("ask", "traderie", old), start, NOW, STALE)
    assert not counts_now(listing("ask", "traderie", NOW, seen=old), start, NOW, STALE)
    # A pasted one has no collector to say it is still up, so the window decides.
    assert counts_now(listing("ask", "d2jsp", NOW, seen=old), start, NOW, STALE)
    assert not counts_now(listing("ask", "d2jsp", old), start, NOW, STALE)


async def test_history_crosses_seasons_for_the_chart(session, item, market, make_listing):
    old = NOW - timedelta(days=90)
    await store_listings(session, item.id, [make_listing("a", 3, observed_at=old)], NOW)
    wanted = PriceQuery(item.id, market)
    quotes = await history(session, wanted, Valuation(SEED_RATES), NOW - timedelta(days=120))
    assert [q.at for q in quotes] == [old]


async def test_recent_listings_are_newest_first(session, item, market, make_listing):
    older = NOW - timedelta(days=1)
    await store_listings(
        session,
        item.id,
        [make_listing("old", 3, observed_at=older), make_listing("new", 4)],
        NOW,
    )
    wanted = PriceQuery(item.id, market)
    rows = await recent_listings(session, wanted, NOW - timedelta(days=2))
    assert [r.external_id for r in rows] == ["new", "old"]
