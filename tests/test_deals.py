"""The deal checker."""

import pytest

from d2_trader.currency import GOODS, RUNES
from d2_trader.deals import Estimate, UnreadableDeal, judge, parse_lot, parse_side, resolve

DEEP = "deep worldstone shard"
VALUES = {
    "ist": Estimate(1.0, 1.0, 1.0),
    "pul": Estimate(0.3, 0.29, 0.31),
    DEEP: Estimate(0.05, 0.047, 0.0532),
    "perfect skull": Estimate(0.05, 0.02, 0.125),
}


@pytest.mark.parametrize(
    "typed, currency",
    [
        ("ist", "ist"),
        ("ist runes", "ist"),
        ("deep worldstone shards", DEEP),
        ("deep shard", DEEP),
        ("amethysts", "perfect amethyst"),
        ("minor keys", "random minor key"),
        ("3x3 key set", "3x3 key set"),
        ("worusk's end", "worusk's end"),
        ("keys of hate", "key of hate"),
    ],
)
def test_a_currency_is_found_by_what_people_call_it(typed, currency):
    assert resolve(typed) == currency


@pytest.mark.parametrize("currency", [*RUNES, *GOODS])
def test_every_currency_is_found_by_its_own_name(currency):
    assert resolve(currency) == currency


def test_an_ambiguous_name_lists_what_it_could_be():
    with pytest.raises(UnreadableDeal, match="Deep Worldstone Shard, Northern"):
        resolve("worldstone shards")


@pytest.mark.parametrize("typed", ["enigma", "rune", "s"])
def test_an_unknown_name_is_refused(typed):
    with pytest.raises(UnreadableDeal, match="No currency"):
        resolve(typed)


@pytest.mark.parametrize(
    "typed, lot",
    [
        ("20 deep shards", (DEEP, 20)),
        ("20x deep shards", (DEEP, 20)),
        ("20 x deep shards", (DEEP, 20)),
        ("2ist", ("ist", 2)),
        ("an ist", ("ist", 1)),
        ("1.5 ist", ("ist", 1.5)),
        ("3x3 key set", ("3x3 key set", 1)),
        ("2 3x3 key sets", ("3x3 key set", 2)),
    ],
)
def test_an_amount_is_read(typed, lot):
    assert parse_lot(typed) == lot


def test_nothing_of_something_is_refused():
    with pytest.raises(UnreadableDeal, match="none at all"):
        parse_lot("0 ist")


@pytest.mark.parametrize(
    "typed", ["2 Pul + 10 Deep Shards", "2 pul, 10 deep shards", "2 pul and 10 deep shards"]
)
def test_a_bundle_is_read(typed):
    assert parse_side(typed) == [("pul", 2), (DEEP, 10)]


def test_an_empty_side_is_refused():
    with pytest.raises(UnreadableDeal, match="both sides"):
        parse_side("  ")


def test_the_operators_example_is_fair():
    """An Ist for 20 worldstone shards, at 20 to the Ist."""
    deal = judge([(DEEP, 20)], [("ist", 1)], VALUES)
    assert deal.verdict == "fair"
    assert deal.ratio == pytest.approx(1.0)
    assert deal.fair_give == pytest.approx(20)


def test_a_clear_gain_is_good_and_its_mirror_bad():
    assert judge([(DEEP, 14)], [("ist", 1)], VALUES).verdict == "good"
    assert judge([("ist", 1)], [(DEEP, 14)], VALUES).verdict == "bad"


def test_a_gain_inside_the_doubt_is_too_close_to_call():
    deal = judge([("perfect skull", 15)], [("ist", 1)], VALUES)
    assert deal.verdict == "unclear"
    assert deal.low < 1 < deal.high


def test_a_bundle_is_summed_and_has_no_single_break_even():
    deal = judge([("pul", 2), (DEEP, 10)], [("ist", 1)], VALUES)
    assert deal.give_ist == pytest.approx(1.1)
    assert deal.fair_give is None


def test_a_certain_side_adds_no_doubt():
    deal = judge([("ist", 1)], [("ist", 2)], VALUES)
    assert deal.low == deal.high == deal.ratio == 2


def test_an_unmeasured_currency_is_not_guessed():
    with pytest.raises(UnreadableDeal, match="Too few trades to value Key of Hate"):
        judge([("key of hate", 3)], [("ist", 1)], VALUES)
