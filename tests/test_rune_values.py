"""Rune values from stored rune sales, on real Postgres."""

from datetime import UTC, date, datetime, timedelta

import pytest

from d2_trader.markets import UNKNOWN_VERSION, MarketKey
from d2_trader.models import Item, Listing
from d2_trader.rune_model import RuneFit, Trade, fit_runes
from d2_trader.currency import SEED_RATES
from d2_trader.seasons import Season
from d2_trader.services.pricing import store_listings
from d2_trader.services.rune_values import (
    CHEAPEST,
    HISTORY_DAYS,
    Ask,
    RuneAsks,
    active_asks,
    bid_offers,
    fit_market,
    model_floor,
    rank_asks,
    report,
    rune_item,
    rune_trades,
)
from d2_trader.sources.types import ParsedListing

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
S15 = Season(15, datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC), "seed")
S16 = Season(16, datetime(2026, 11, 13, tzinfo=UTC), None, "seed")
SEASONS = [S15, S16]
LADDER = MarketKey()


def sold(n, alternatives, at, market=LADDER, kind="sold", quantity=1):
    return ParsedListing("traderie", f"x{n}", kind, market, alternatives, at, quantity=quantity)


async def stored(session, rune, listings):
    item = await rune_item(session, rune)
    await store_listings(session, item.id, listings, NOW)
    return item


# --- the rune's catalog row -------------------------------------------------------


async def test_a_missing_rune_is_added_as_diablo2io_names_it(session):
    item = await rune_item(session, "lo", "3632079454")
    assert (item.name, item.category, item.traderie_id) == ("Lo", "misc", "3632079454")
    assert (await rune_item(session, "lo")).id == item.id


async def test_a_rune_already_in_the_catalog_is_reused_under_either_name(session):
    row = Item(name="Ber Rune", category="misc", traderie_id="keep")
    session.add(row)
    await session.flush()
    assert (await rune_item(session, "ber", "other")).id == row.id
    assert row.traderie_id == "keep"


# --- which sales are read ---------------------------------------------------------


async def test_only_this_markets_rune_sales_since_the_floor_are_read(session):
    recent = NOW - timedelta(days=2)
    await stored(
        session,
        "lo",
        [
            sold(1, [[("ist", 9.0)]], recent),
            sold(2, [[("ist", 9.0)]], recent, kind="ask"),  # Not a sale.
            sold(3, [[("ist", 9.0)]], NOW - timedelta(days=30)),  # Too old.
            sold(4, [[("ist", 9.0)]], recent, MarketKey(mode="hardcore")),
            sold(5, [[("ist", 9.0)]], recent, MarketKey(game_version=UNKNOWN_VERSION)),
            sold(6, [[("ohm", 2.0)], [("ist", 9.0)]], recent),  # Which was paid?
        ],
    )
    item = Item(name="Enigma", category="runewords")
    session.add(item)
    await session.flush()
    await store_listings(session, item.id, [sold(7, [[("ber", 1.0)]], recent)], NOW)
    trades = await rune_trades(session, LADDER, NOW - timedelta(days=7))
    assert sorted(t.paid for t in trades) == [(("ist", 9.0),), (("ist", 9.0),)]
    assert {t.rune for t in trades} == {"lo"}


def test_a_ladder_reads_from_its_season_start():
    assert model_floor(SEASONS, True, NOW) == S16.starts_at


def test_non_ladder_reads_from_the_last_transfer():
    assert model_floor(SEASONS, False, NOW) == S15.ends_at


def test_with_no_calendar_a_fixed_span_is_read():
    assert model_floor([], True, NOW) == NOW - timedelta(days=HISTORY_DAYS)


async def test_last_seasons_sales_never_value_this_seasons_lo(session):
    await stored(
        session,
        "lo",
        [sold(n, [[("ist", 30.0)]], S16.starts_at - timedelta(days=n + 1)) for n in range(10)]
        + [sold(n + 10, [[("ist", 8.0)]], NOW - timedelta(days=n)) for n in range(6)],
    )
    fit = await fit_market(session, LADDER, SEASONS, NOW)
    assert fit.paths["lo"][0].day == S16.starts_at.date()
    assert fit.today()["lo"] == pytest.approx(8, rel=0.05)


async def test_lo_is_valued_from_sales_of_other_runes(session):
    """Lo sold only for Ber; Ber sold for Ist. Lo still gets a value in Ist."""
    days = [NOW - timedelta(days=n) for n in range(6)]
    await stored(session, "lo", [sold(n, [[("ber", 0.5)]], at) for n, at in enumerate(days)])
    await stored(session, "ber", [sold(n + 10, [[("ist", 16.0)]], at) for n, at in enumerate(days)])
    fit = await fit_market(session, LADDER, SEASONS, NOW)
    assert fit.today()["lo"] == pytest.approx(8, rel=0.08)
    assert fit.direct["lo"] == 6


# --- the report -------------------------------------------------------------------


def test_the_report_says_when_there_is_nothing_yet():
    [line] = report(RuneFit({}, {}, 0), "lo", LADDER)
    assert "run rates" in line


def test_the_report_gives_today_with_its_interval_and_the_recent_days():
    start = date(2026, 11, 13)
    trades = [Trade(start + timedelta(days=n), "lo", 1, (("ist", 8.0),)) for n in range(8)]
    fit = fit_runes(trades, SEED_RATES, until=start + timedelta(days=7))
    lines = report(fit, "lo", LADDER, days=3)
    assert lines[0].startswith("Lo · PC · Softcore · Ladder · RotW · 2026-11-20")
    assert "8.00 Ist" in lines[1] and "95%" in lines[1] and "Ber" in lines[1]
    assert "8 name Lo" in lines[2]
    assert len(lines) == 5 + 3
    assert lines[-1].lstrip().startswith("2026-11-20")


# --- the cheapest asks ------------------------------------------------------------

RATES = {**SEED_RATES, "lo": 5.0, "ohm": 3.0, "ber": 14.0}


def ask(alternatives, quantity=1, seen=NOW):
    return Listing(alternatives=alternatives, quantity=quantity, last_seen_at=seen)


def test_asks_are_ranked_cheapest_first_per_rune():
    rows = [("Lo", ask([[["ist", v]]])) for v in (6.0, 4.5, 5.0)] + [("Ber", ask([[["ist", 14]]]))]
    ranked = rank_asks(rows, RATES)
    assert [a.value for a in ranked["lo"].cheapest] == [4.5, 5.0, 6.0]
    assert [a.value for a in ranked["ber"].cheapest] == [14.0]
    assert isinstance(ranked["lo"], RuneAsks)
    assert all(isinstance(a, Ask) for a in ranked["lo"].cheapest)


def test_only_the_cheapest_ten_are_kept():
    rows = [("Lo", ask([[["ist", 5 + n / 10]]])) for n in range(15)]
    cheapest = rank_asks(rows, RATES)["lo"].cheapest
    assert len(cheapest) == CHEAPEST == 10
    assert cheapest[0].value == 5.0


def test_an_ask_is_worth_its_cheapest_alternative_per_rune_of_the_lot():
    """3 Lo for "2 Ber or 15 Ist": the seller takes 15 Ist, 5 Ist a Lo."""
    rows = [("Lo Rune", ask([[["ber", 2.0]], [["ist", 15.0]]], quantity=3))]
    assert rank_asks(rows, RATES)["lo"].cheapest[0].value == pytest.approx(5.0)


def test_bait_is_hidden_and_counted():
    rows = [("Lo", ask([[["ist", v]]])) for v in (5.0, 5.2, 4.9, 5.1, 5.3, 0.01)]
    ranked = rank_asks(rows, RATES)["lo"]
    assert ranked.hidden == 1
    assert min(a.value for a in ranked.cheapest) == 4.9


def test_an_ask_nothing_can_value_is_left_out():
    rows = [("Lo", ask([[["mystery charm", 1.0]]])), ("Lo", ask([[["ist", 5.0]]]))]
    assert len(rank_asks(rows, RATES)["lo"].cheapest) == 1


def test_a_runes_asks_say_when_they_were_last_seen():
    rows = [("Lo", ask([[["ist", 5.0]]], seen=NOW - timedelta(hours=h))) for h in (1, 3)]
    assert rank_asks(rows, RATES)["lo"].seen_at == NOW - timedelta(hours=1)


def bid(alternatives, quantity=1):
    return Listing(kind="bid", alternatives=alternatives, quantity=quantity, last_seen_at=NOW)


def test_a_bid_is_an_offer_of_the_rune_it_pays_in():
    """Observed: wants 2 Ist, has 5 Um."""
    [(rune, offer)] = bid_offers("ist", bid([[["um", 5.0]]], quantity=2), RATES)
    assert (rune, offer.wanted, offer.offered) == ("um", "ist", [("um", 5.0)])
    assert offer.value == pytest.approx(0.4)


def test_a_bundle_is_offered_under_its_main_rune_at_the_bundles_discount():
    [(rune, offer)] = bid_offers("ber", bid([[["ohm", 1.0], ["lo", 5.0]]]), RATES)
    assert rune == "lo"
    assert offer.value / RATES["lo"] == pytest.approx(14 / 28)


def test_each_alternative_of_a_bid_is_its_own_offer():
    found = bid_offers("ber", bid([[["lo", 3.0]], [["ohm", 5.0]], [["lo", 4.0]]]), RATES)
    assert {rune: offer.value for rune, offer in found} == {"lo": 3.5, "ohm": 2.8}


@pytest.mark.parametrize(
    "paid", [[], [["jewel", 10.0], ["lo", 1.0]], [["ist", 1.0]], [["lo", 0.0]]]
)
def test_a_bid_that_offers_no_other_runes_is_left_out(paid):
    assert bid_offers("ist", bid([paid]), RATES) == []


def test_a_bid_for_something_unvalued_is_left_out():
    assert bid_offers("mystery", bid([[["lo", 1.0]]]), RATES) == []


def test_asks_and_bids_are_ranked_together():
    rows = [("Lo", ask([[["ist", 4.5]]])), ("Ist", bid([[["lo", 1.0]]], quantity=4))]
    ranked = rank_asks(rows, RATES)
    assert [(a.value, a.wanted) for a in ranked["lo"].cheapest] == [(4.0, "ist"), (4.5, None)]
    assert "ist" not in ranked


async def test_an_open_bid_is_active(session):
    ist = await rune_item(session, "ist")
    await store_listings(session, ist.id, [sold(1, [[("lo", 1.0)]], NOW, kind="bid")], NOW)
    found = await active_asks(session, LADDER, NOW - timedelta(hours=13))
    assert [(name, row.kind) for name, row in found] == [("Ist", "bid")]


async def test_only_asks_still_seen_in_this_market_are_active(session):
    lo = await rune_item(session, "lo")
    await store_listings(session, lo.id, [sold(1, [[("ist", 5.0)]], NOW, kind="ask")], NOW)
    await store_listings(session, lo.id, [sold(2, [[("ist", 4.0)]], NOW)], NOW)  # A sale.
    hardcore = sold(3, [[("ist", 4.0)]], NOW, MarketKey(mode="hardcore"), kind="ask")
    await store_listings(session, lo.id, [hardcore], NOW)
    gone = sold(4, [[("ist", 4.0)]], NOW, kind="ask")
    await store_listings(session, lo.id, [gone], NOW - timedelta(hours=30))
    found = await active_asks(session, LADDER, NOW - timedelta(hours=13))
    assert [(name, row.external_id) for name, row in found] == [("Lo", "x1")]
