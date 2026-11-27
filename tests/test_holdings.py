"""What the operator holds, saved per market."""

import pytest
from sqlalchemy import select

from d2_trader.currency import GOODS_BY_FAMILY, RUNES
from d2_trader.markets import MarketKey
from d2_trader.models import Holding
from d2_trader.services.holdings import (
    FIELD,
    BadHolding,
    HoldingGroup,
    holding_groups,
    load_holdings,
    read_quantities,
    save_holdings,
)

LADDER = MarketKey()
NON_LADDER = MarketKey(ladder=False)


def fields(**counts):
    return [(f"{FIELD}{c.replace('_', ' ')}", v) for c, v in counts.items()]


# --- reading the form -------------------------------------------------------------


def test_the_forms_counts_are_read_and_blanks_and_zeros_hold_none():
    form = fields(pul="15", um=" 10 ", mal="", gul="0", perfect_amethyst="2")
    form.append(("platform", "PC"))  # Not a holding.
    assert read_quantities(form) == {"pul": 15, "um": 10, "perfect amethyst": 2}


def test_an_empty_form_holds_nothing():
    assert read_quantities([]) == {}


@pytest.mark.parametrize(
    ("form", "says"),
    [
        (fields(pul="1.5"), "Pul: '1.5' is not a whole number"),
        (fields(pul="lots"), "not a whole number"),
        (fields(vex="-2"), "Vex: a count cannot be below zero"),
        ([(f"{FIELD}jewel", "3")], "No currency called 'jewel'"),
    ],
)
def test_a_bad_count_is_refused_with_what_was_wrong(form, says):
    with pytest.raises(BadHolding, match=says):
        read_quantities(form)


# --- the form's layout ------------------------------------------------------------


def test_the_traded_runes_are_open_and_the_rest_folded_until_held():
    groups = holding_groups({"pul": 15})
    assert all(isinstance(g, HoldingGroup) for g in groups)
    runes, low, *goods = groups
    assert (runes.title, runes.folded) == ("Runes", False)
    assert [c for c, _, _ in runes.rows] == RUNES[RUNES.index("pul") :]
    assert ("pul", "Pul", 15) in runes.rows and ("ber", "Ber", 0) in runes.rows
    assert (low.title, low.folded) == ("Low runes", True)
    assert next(c for c, _, _ in low.rows) == "el"
    assert [g.title for g in goods] == list(GOODS_BY_FAMILY)
    assert all(g.folded for g in goods)


def test_a_folded_group_opens_once_something_in_it_is_held():
    groups = {g.title: g for g in holding_groups({"perfect amethyst": 2, "ko": 1})}
    assert not groups["Gems"].folded and not groups["Low runes"].folded
    assert ("perfect amethyst", "Perfect Amethyst", 2) in groups["Gems"].rows
    assert groups["Keys"].folded


# --- saving -----------------------------------------------------------------------


async def test_nothing_is_held_before_anything_is_saved(session):
    assert await load_holdings(session, LADDER) == {}


async def test_saved_holdings_come_back(session):
    await save_holdings(session, LADDER, {"pul": 15, "vex": 4, "perfect amethyst": 2})
    assert await load_holdings(session, LADDER) == {"pul": 15, "vex": 4, "perfect amethyst": 2}


async def test_saving_replaces_what_was_held(session):
    await save_holdings(session, LADDER, {"pul": 15, "vex": 4})
    await save_holdings(session, LADDER, {"pul": 11, "ist": 1, "um": 0})
    assert await load_holdings(session, LADDER) == {"pul": 11, "ist": 1}


async def test_each_market_keeps_its_own_holdings(session):
    await save_holdings(session, LADDER, {"pul": 15})
    await save_holdings(session, NON_LADDER, {"ber": 1})
    assert await load_holdings(session, LADDER) == {"pul": 15}
    assert await load_holdings(session, NON_LADDER) == {"ber": 1}
    assert await load_holdings(session, MarketKey(mode="hardcore")) == {}


async def test_a_saved_holding_is_one_holding_row_per_currency(session):
    """`Holding` is the table `save_holdings` writes: a row per held currency."""
    await save_holdings(session, LADDER, {"pul": 15, "vex": 4})
    rows = (await session.scalars(select(Holding))).all()
    assert sorted((row.currency, row.quantity) for row in rows) == [("pul", 15), ("vex", 4)]
