from datetime import UTC, datetime, timedelta

import pytest_asyncio
from conftest import NOW
from sqlalchemy import select

from d2_trader.markets import MarketKey
from d2_trader.models import AlertRule, Item, Notification, Watchlist
from d2_trader.services.calendar import current_state, ensure_seed_seasons
from d2_trader.services.pricing import PriceQuery, store_listings
from d2_trader.services.rates import set_manual_rate
from d2_trader.services.watch import (
    add_watch,
    alert_message,
    describe_rule,
    enabled_watches,
    evaluate_all,
    get_watch,
    judge_rule,
    list_watchlists,
    query_for,
    threshold_in_ist,
    view_for,
)

GAP = datetime(2026, 11, 11, tzinfo=UTC)
SEASON_16 = datetime(2026, 11, 15, tzinfo=UTC)


@pytest_asyncio.fixture
async def watch(session, item, market):
    await ensure_seed_seasons(session)
    watchlist = Watchlist(name="Shopping")
    session.add(watchlist)
    await session.flush()
    return await add_watch(session, watchlist.id, item.id, market, None)


def rule_on(watch, **overrides):
    defaults = {
        "metric": "sold_median",
        "direction": "at_or_below",
        "threshold_amount": 1.0,
        "threshold_currency": "ber",
        "armed": True,
        "enabled": True,
        "cooldown_hours": 12,
    }
    return AlertRule(watch_item_id=watch.id, **{**defaults, **overrides})


async def sales(session, item, make_listing, price, at, count=6, prefix="s"):
    listings = [make_listing(f"{prefix}{i}", price, observed_at=at) for i in range(count)]
    await store_listings(session, item.id, listings, at)


async def fired(session) -> list[Notification]:
    return list((await session.execute(select(Notification))).scalars())


async def test_a_watch_asks_for_its_own_item_market_and_copies(session, watch, item, market):
    assert query_for(watch) == PriceQuery(item.id, market, None)


async def test_adding_a_watch_twice_is_one_watch(session, watch, item, market):
    again = await add_watch(session, watch.watchlist_id, item.id, market, None)
    assert again.id == watch.id


async def test_one_item_can_be_watched_on_two_markets(session, watch, item):
    other = await add_watch(session, watch.watchlist_id, item.id, MarketKey(ladder=False), None)
    assert other.id != watch.id


async def test_ethereal_and_normal_copies_are_separate_watches(session, watch, item, market):
    other = await add_watch(session, watch.watchlist_id, item.id, market, True)
    assert other.id != watch.id


async def test_lists_load_with_their_entries(session, watch):
    [watchlist] = await list_watchlists(session)
    assert [e.id for e in watchlist.entries] == [watch.id]


async def test_deleting_a_list_removes_its_watches_and_rules(session, watch):
    session.add(rule_on(watch))
    await session.flush()
    await session.delete(await session.get(Watchlist, watch.watchlist_id))
    await session.flush()
    session.expire_all()
    assert await enabled_watches(session) == []
    assert (await session.execute(select(AlertRule))).first() is None


async def test_a_missing_watch_is_none(session):
    assert await get_watch(session, 99999) is None


async def test_thresholds_follow_the_rate_they_were_written_in(session, watch):
    rule = rule_on(watch, threshold_amount=2.0)
    assert threshold_in_ist(rule, {"ber": 10.0}) == 20.0
    assert threshold_in_ist(rule, {"ber": 15.0}) == 30.0
    assert threshold_in_ist(rule, {}) is None


async def test_rule_description_reads_like_a_sentence(watch):
    assert describe_rule(rule_on(watch)) == "Median sold at or below 1 Ber"


async def test_a_price_below_threshold_notifies(session, settings, watch, item, make_listing):
    session.add(rule_on(watch))
    await sales(session, item, make_listing, 5, NOW)
    assert await evaluate_all(session, settings, NOW) == 1
    [notification] = await fired(session)
    assert notification.title == "Test Crest: alert reached"
    assert notification.url == f"/watch/{watch.id}"


async def test_a_price_above_threshold_is_quiet(session, settings, watch, item, make_listing):
    rule = rule_on(watch)
    session.add(rule)
    await sales(session, item, make_listing, 500, NOW)
    assert await evaluate_all(session, settings, NOW) == 0
    assert rule.last_reason == "threshold not reached"


async def test_it_fires_once_per_crossing(session, settings, watch, item, make_listing):
    session.add(rule_on(watch))
    await sales(session, item, make_listing, 5, NOW)
    await evaluate_all(session, settings, NOW)
    assert await evaluate_all(session, settings, NOW + timedelta(days=1)) == 0
    assert len(await fired(session)) == 1


async def test_a_paused_rule_is_skipped(session, settings, watch, item, make_listing):
    session.add(rule_on(watch, enabled=False))
    await sales(session, item, make_listing, 5, NOW)
    assert await evaluate_all(session, settings, NOW) == 0


async def test_ladder_rules_stay_quiet_between_seasons(
    session, settings, watch, item, make_listing
):
    """Stale leftovers are cheap, and that must not look like a bargain."""
    rule = rule_on(watch)
    session.add(rule)
    await sales(session, item, make_listing, 5, GAP - timedelta(hours=1))
    assert await evaluate_all(session, settings, GAP) == 0
    assert "between seasons" in rule.last_reason


async def test_non_ladder_rules_keep_working_between_seasons(
    session, settings, watch, item, make_listing
):
    non_ladder = await add_watch(
        session, watch.watchlist_id, item.id, MarketKey(ladder=False), None
    )
    session.add(rule_on(non_ladder))
    market = MarketKey(ladder=False)
    listings = [make_listing(f"n{i}", 5, observed_at=GAP, market=market) for i in range(6)]
    await store_listings(session, item.id, listings, GAP)
    assert await evaluate_all(session, settings, GAP) == 1


async def test_a_new_season_judges_only_its_own_prices(
    session, settings, watch, item, make_listing
):
    session.add(rule_on(watch))
    await sales(session, item, make_listing, 5, SEASON_16 - timedelta(days=8))
    assert await evaluate_all(session, settings, SEASON_16) == 0


async def test_a_rule_in_a_currency_with_no_rate_says_so(session, watch, mid_season, settings):
    rule = rule_on(watch, threshold_currency="mystery")
    session.add(rule)
    await session.flush()
    view, valuation = await view_for(session, watch, mid_season, NOW, settings)
    decision, _ = await judge_rule(rule, view, valuation.rates, NOW)
    assert decision.fire is False
    assert "no rate" in rule.last_reason


async def test_an_fg_rule_follows_the_measured_fg_rate(
    session, settings, watch, item, make_listing
):
    """A rule in fg is judged at what fg is worth now, as rune prices measured it."""
    ber = Item(name="Ber", category="misc")
    session.add(ber)
    await session.flush()
    session.add(rule_on(watch, threshold_amount=100.0, threshold_currency="fg"))
    await sales(session, item, make_listing, 12.5, NOW)  # 1 Ber, in Ist.
    # At the seed (1 fg = 0.1 Ist) 100 fg is 10 Ist: under the price, so no alert.
    assert await evaluate_all(session, settings, NOW) == 0
    # d2jsp then says a Ber is 90 fg: 1 fg = 0.139 Ist, so 100 fg is 13.9 Ist. Alert.
    # (Not exactly a Ber's worth: a threshold equal to the price is a coin toss in floats.)
    bers = [
        make_listing(f"b{i}", 90, currency="fg", source="d2jsp", observed_at=NOW) for i in range(3)
    ]
    await store_listings(session, ber.id, bers, NOW)
    assert await evaluate_all(session, settings, NOW + timedelta(minutes=1)) == 1


async def test_a_manual_rate_moves_a_rule_across_the_line(
    session, settings, watch, item, make_listing
):
    session.add(rule_on(watch, threshold_amount=1.0, threshold_currency="fg"))
    await sales(session, item, make_listing, 5, NOW)
    assert await evaluate_all(session, settings, NOW) == 0
    await set_manual_rate(session, "fg", 10.0)
    assert await evaluate_all(session, settings, NOW) == 1


async def test_the_message_carries_range_and_seasonal_context(
    session, settings, watch, item, make_listing
):
    late = datetime(2026, 11, 5, tzinfo=UTC)
    rule = rule_on(watch)
    session.add(rule)
    await sales(session, item, make_listing, 5, late)
    await session.flush()
    state, _ = await current_state(session, late)
    view, valuation = await view_for(session, watch, state, late, settings)
    body = alert_message(watch, rule, view, 5.0, valuation.rates, late).body
    assert "PC · Softcore · Ladder · RotW" in body
    assert "6 samples" in body
    assert "ending" in body
    # Regression: the price was in its own largest unit, not the one the rule was set in.
    assert "at or below 1 Ber: now 0.4 Ber" in body
    assert "Fair range 0.4 to 0.4 Ber" in body
