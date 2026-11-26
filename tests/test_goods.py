"""Goods valued against runes."""

from datetime import date, timedelta

import pytest

from d2_trader.currency import GOODS, GOODS_BY_FAMILY, RUNES, SEED_RATES, display_name
from d2_trader.goods import READINGS_PER_DAY, GoodsFit, Sale, fit_goods, reading, thin
from d2_trader.trend import PathPoint

DAY = date(2026, 11, 20)
DEEP = "deep worldstone shard"
VALUES = {"ist": 1.0, "pul": 0.25, "lo": 5.0}


def flat(value, days=5):
    return [PathPoint(DAY + timedelta(days=n), value, value, value, 1) for n in range(days)]


PATHS = {rune: flat(value) for rune, value in VALUES.items()}


def sale(item, alternatives, quantity=1, day=DAY):
    return Sale(item, day, quantity, alternatives)


def test_goods_are_named_once_and_are_not_runes():
    assert len(GOODS) == sum(len(f) for f in GOODS_BY_FAMILY.values())
    assert not set(GOODS) & set(RUNES)


def test_every_seeded_currency_but_runes_and_fg_is_a_good():
    assert set(SEED_RATES) - set(RUNES) - {"fg"} <= set(GOODS)


def test_names_are_written_as_the_game_writes_them():
    assert display_name("key of terror") == "Key of Terror"
    assert display_name("worusk's end") == "Worusk's End"
    assert display_name("skullder's ire") == "Skullder's Ire"
    assert display_name("ber") == "Ber"


def test_a_rune_sold_for_a_good_prices_the_good():
    assert reading(sale("ist", [[[DEEP, 20.0]]]), VALUES) == (DEEP, 0.05)


def test_a_rune_lot_sold_for_a_good_counts_the_whole_lot():
    assert reading(sale("pul", [[[DEEP, 10.0]]], quantity=2), VALUES) == (DEEP, 0.05)


def test_a_good_sold_for_runes_is_priced_per_item():
    found = reading(sale(DEEP, [[["ist", 1.0], ["pul", 2.0]]], quantity=30), VALUES)
    assert found == (DEEP, pytest.approx(0.05))


@pytest.mark.parametrize(
    "item, alternatives",
    [
        ("ist", [[[DEEP, 20.0]], [["perfect amethyst", 25.0]]]),  # Which was paid?
        ("ist", [[["pul", 1.0], [DEEP, 10.0]]]),  # Mixed.
        ("ist", [[["pul", 4.0]]]),  # No good in it.
        ("ist", [[[DEEP, 0.0]]]),
        ("ist", [[]]),
        ("ist", []),
        (DEEP, [[["perfect amethyst", 1.0]]]),  # No rune to measure by.
        ("zod", [[[DEEP, 400.0]]]),  # A rune with no value that day.
        (DEEP, [[["zod", 1.0]]]),
        ("enigma", [[[DEEP, 100.0]]]),
    ],
)
def test_a_sale_that_does_not_price_one_good_is_left_out(item, alternatives):
    assert reading(sale(item, alternatives), VALUES) is None


def test_a_busy_day_is_thinned_across_its_spread():
    kept = thin([(DAY, float(v)) for v in range(120)] + [(DAY + timedelta(days=1), 7.0)])
    values = sorted(v for d, v in kept if d == DAY)
    assert len(values) == READINGS_PER_DAY
    assert values[0] < 10 and values[-1] > 109
    assert (DAY + timedelta(days=1), 7.0) in kept


def test_a_good_is_valued_from_both_kinds_of_trade():
    days = [DAY + timedelta(days=n) for n in range(5)]
    sales = [sale("ist", [[[DEEP, 20.0]]], day=d) for d in days]
    sales += [sale(DEEP, [[["pul", 1.0]]], quantity=5, day=d) for d in days]
    fit = fit_goods(sales, PATHS, until=days[-1])
    assert fit.today()[DEEP] == pytest.approx(0.05, rel=0.02)
    assert fit.direct[DEEP] == len(fit.observed[DEEP]) == 10


def test_a_sale_is_valued_at_its_own_days_rune_values():
    paths = {"lo": [*flat(5.0, 1), PathPoint(DAY + timedelta(days=1), 10.0, 10.0, 10.0, 1)]}
    later = DAY + timedelta(days=1)
    fit = fit_goods([sale("lo", [[[DEEP, 100.0]]], day=later)], paths, until=later)
    assert fit.observed[DEEP] == [(later, 0.1)]


def test_a_day_without_rune_values_prices_nothing():
    early = sale("ist", [[[DEEP, 20.0]]], day=DAY - timedelta(days=1))
    assert fit_goods([early], PATHS, until=DAY) == GoodsFit()


def test_too_few_trades_are_shown_but_not_measured():
    fit = fit_goods([sale("ist", [[[DEEP, 20.0]]])] * 4, PATHS, until=DAY)
    assert DEEP in fit.today() and fit.measured() == {}
    fit = fit_goods([sale("ist", [[[DEEP, 20.0]]])] * 5, PATHS, until=DAY)
    assert set(fit.measured()) == {DEEP}
