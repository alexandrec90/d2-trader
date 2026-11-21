from d2_trader.currency import (
    NO_VALUE,
    SEED_RATES,
    alternative_value,
    format_in,
    format_like,
    format_range,
    format_value,
    normalize_currency,
    unit_value,
)

RATES = {"ist": 1.0, "vex": 2.0, "ber": 10.0, "lo": 5.0, "zod": 20.0, "pul": 0.5, "fg": 0.1}


def test_rune_spellings_are_one_currency():
    assert normalize_currency("Ber Rune") == normalize_currency("ber") == "ber"


def test_forum_gold_spellings_are_one_currency():
    assert normalize_currency("Forum Gold") == normalize_currency("FG") == "fg"


def test_a_multi_word_currency_keeps_its_name():
    assert normalize_currency("Perfect  Amethyst") == "perfect amethyst"


def test_alternative_value_adds_a_bundle():
    assert alternative_value([("ber", 1), ("ist", 2)], RATES) == 12


def test_alternative_value_is_none_when_any_part_is_unknown():
    assert alternative_value([("ber", 1), ("mystery", 1)], RATES) is None


def test_a_seller_is_held_to_the_cheapest_alternative():
    assert unit_value([[("ber", 1)], [("ist", 3)]], RATES, "ask") == 3


def test_a_buyer_is_held_to_the_best_alternative():
    assert unit_value([[("ber", 1)], [("ist", 3)]], RATES, "bid") == 10


def test_an_unknown_alternative_does_not_void_a_known_one():
    assert unit_value([[("mystery", 1)], [("ist", 3)]], RATES, "ask") == 3


def test_nothing_valuable_is_none_rather_than_zero():
    assert unit_value([[("mystery", 1)]], RATES, "ask") is None
    assert unit_value([], RATES, "ask") is None


def test_a_lot_price_is_divided_per_item():
    # Observed on Traderie: "6 Pul for 1 Vex".
    assert unit_value([[("vex", 1)]], RATES, "sold", lot_size=6) == 2 / 6


def test_a_zero_lot_size_is_treated_as_one():
    # Traderie really does report `amount: 0` on some completed listings.
    assert unit_value([[("ist", 2)]], RATES, "sold", lot_size=0) == 2


def test_format_picks_the_largest_unit_reached():
    assert format_value(25, RATES) == "1.25 Zod"
    assert format_value(15, RATES) == "1.5 Ber"
    assert format_value(6, RATES) == "1.2 Lo"
    assert format_value(1.5, RATES) == "1.5 Ist"


def test_format_shows_small_values_in_ist():
    assert format_value(0.5, RATES) == "0.5 Ist"


def test_format_marks_a_missing_value():
    assert format_value(None, RATES) == NO_VALUE


def test_a_range_uses_one_unit_so_its_ends_can_be_compared():
    # Regression: each end picked its own unit, giving "1.8 Lo to 1.05 Ber".
    assert format_range(9, 10.5, RATES) == "0.9 to 1.05 Ber"


def test_a_range_below_every_rune_stays_in_ist():
    assert format_range(0.2, 0.5, RATES) == "0.2 to 0.5 Ist"


def test_a_range_with_a_missing_end_is_marked():
    assert format_range(None, 2, RATES) == NO_VALUE


def test_a_value_is_stated_in_the_unit_of_its_interval():
    # Regression: "1.99 Lo" over an interval of "0.909 to 1.01 Ber".
    assert format_like(9.5, 10.1, RATES) == "0.95 Ber"
    assert format_like(0.5, 0.9, RATES) == "0.5 Ist"
    assert format_like(None, 10, RATES) == NO_VALUE


def test_a_value_can_be_stated_in_a_chosen_currency():
    assert format_in(12.5, "ber", RATES) == "1.25 Ber"


def test_seeds_rank_high_runes_in_the_order_they_trade():
    assert SEED_RATES["ist"] < SEED_RATES["vex"] < SEED_RATES["lo"] < SEED_RATES["ber"]
    assert SEED_RATES["ist"] == 1.0
