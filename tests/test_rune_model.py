"""The joint rune model, checked against simulated markets whose true values are known."""

import math
import random
from datetime import date, timedelta

import numpy as np
import pytest

from d2_trader.currency import SEED_RATES
from d2_trader.rune_model import (
    STATE_RUNES,
    Candidate,
    Trade,
    fit_runes,
    grid,
    implied_value,
    process_noise,
    trade_from,
    weigh,
)

START = date(2026, 9, 1)


def day(n: int) -> date:
    return START + timedelta(days=n)


def walk(rng: random.Random, start: float, days: int, step: float = 0.02) -> list[float]:
    """A true value that wanders, one per day."""
    values = [start]
    for _ in range(days - 1):
        values.append(values[-1] * math.exp(rng.gauss(0, step)))
    return values


def sale(rng, n, rune, value, paid_in, paid_value, noise=0.05, lot=1) -> Trade:
    """`lot` of `rune` sold for however much `paid_in` that was worth, give or take."""
    quantity = lot * value / paid_value * math.exp(rng.gauss(0, noise))
    return Trade(day(n), rune, lot, ((paid_in, quantity),))


# --- reading a sale ---------------------------------------------------------------


def test_a_plain_rune_sale_is_a_trade():
    trade = trade_from("lo", day(0), 2, [[["ohm", 3.0]]])
    assert trade == Trade(day(0), "lo", 2.0, (("ohm", 3.0),))


def test_a_mixed_payment_is_kept_whole():
    trade = trade_from("ber", day(0), 1, [[["lo", 1.0], ["ist", 4.0]]])
    assert trade.paid == (("lo", 1.0), ("ist", 4.0))
    assert trade.involves() == {"ber", "lo", "ist"}


@pytest.mark.parametrize(
    "rune, alternatives",
    [
        ("lo", [[["ohm", 2.0]], [["ist", 6.0]]]),  # Which alternative was paid is unknown.
        ("lo", [[["perfect skull", 9.0]]]),  # Not a rune.
        ("lo", [[["lo", 1.0], ["ist", 1.0]]]),  # Paid partly in itself.
        ("lo", [[["ist", 0.0]]]),  # Paid nothing.
        ("lo", [[]]),
        ("lo", []),
        ("enigma", [[["ber", 1.0]]]),  # Not a rune being sold.
    ],
)
def test_a_sale_that_cannot_be_read_as_runes_is_left_out(rune, alternatives):
    assert trade_from(rune, day(0), 1, alternatives) is None


def test_a_lot_size_below_one_counts_as_one():
    assert trade_from("lo", day(0), 0, [[["ist", 6.0]]]).lot == 1.0


# --- the pieces -------------------------------------------------------------------


def test_process_noise_has_the_shared_part_off_the_diagonal():
    q = process_noise(Candidate(step=0.1, shared=0.5, noise=0.1))
    assert q[0, 0] == pytest.approx(0.01)
    assert q[0, 1] == pytest.approx(0.005)
    assert np.all(np.linalg.eigvalsh(q) > 0)


def test_weights_sum_to_one_and_drop_the_hopeless():
    weights = weigh([0.0, -1.0, -50.0])
    assert sum(weights) == pytest.approx(1.0)
    assert weights[2] == 0.0
    assert weights[0] > weights[1] > 0


def test_the_grid_spans_every_combination():
    assert len(grid()) == len({(c.step, c.shared, c.noise) for c in grid()}) == 48


# --- fitting ----------------------------------------------------------------------


def test_no_trades_no_fit():
    fit = fit_runes([], SEED_RATES, until=day(5))
    assert fit.paths == {} and fit.today() == {} and fit.trades == 0


def test_trades_outside_the_span_are_ignored():
    trades = [Trade(day(0), "lo", 1, (("ist", 6.0),)), Trade(day(9), "lo", 1, (("ist", 60.0),))]
    fit = fit_runes(trades, SEED_RATES, until=day(5), start=day(0))
    assert fit.trades == 1
    assert [p.day for p in fit.paths["lo"]] == [day(n) for n in range(6)]


def test_ist_is_the_unit():
    fit = fit_runes([Trade(day(0), "lo", 1, (("ist", 6.0),))], SEED_RATES, until=day(2))
    assert all(p.value == p.low == p.high == 1.0 for p in fit.paths["ist"])


def test_a_direct_price_is_found_and_bracketed():
    rng = random.Random(3)
    trades = [sale(rng, n, "lo", 9.0, "ist", 1.0) for n in range(20) for _ in range(3)]
    point = fit_runes(trades, SEED_RATES, until=day(19)).paths["lo"][-1]
    assert point.value == pytest.approx(9.0, rel=0.06)
    assert point.low < 9.0 < point.high


def test_a_lot_is_priced_per_rune():
    trades = [Trade(day(n), "ohm", 3, (("ist", 15.0),)) for n in range(10)]
    assert fit_runes(trades, SEED_RATES, until=day(9)).today()["ohm"] == pytest.approx(5, rel=0.03)


def test_lo_is_valued_through_trades_that_never_pair_it_with_ist():
    """Lo only ever trades for Ber, and Ber for Ist: Lo's value in Ist still follows."""
    rng = random.Random(5)
    trades = []
    for n in range(30):
        trades += [sale(rng, n, "lo", 7.0, "ber", 14.0) for _ in range(3)]  # Half a Ber.
        trades += [sale(rng, n, "ber", 14.0, "ist", 1.0) for _ in range(3)]
    fit = fit_runes(trades, SEED_RATES, until=day(29))
    lo = fit.paths["lo"][-1]
    assert lo.value == pytest.approx(7.0, rel=0.08)
    assert lo.low < 7.0 < lo.high
    assert fit.direct["lo"] == 90 and fit.direct["ist"] == 90


def test_trades_without_lo_tighten_lo():
    """Adding Ber-for-Ist sales, which never name Lo, narrows Lo's interval."""
    rng = random.Random(8)
    lo_trades = [sale(rng, n, "lo", 7.0, "ber", 14.0) for n in range(20)]
    lo_trades += [sale(rng, n, "lo", 7.0, "ist", 1.0) for n in range(0, 20, 5)]
    ber_trades = [sale(rng, n, "ber", 14.0, "ist", 1.0) for n in range(20) for _ in range(4)]

    def width(trades):
        point = fit_runes(trades, SEED_RATES, until=day(19)).paths["lo"][-1]
        return point.high / point.low

    assert width(lo_trades + ber_trades) < width(lo_trades)


def test_a_mixed_payment_is_valued_as_a_sum():
    """Ber sold for one Lo plus four Ist, with Lo pinned at 8: Ber is about 12."""
    trades = [Trade(day(n), "lo", 1, (("ist", 8.0),)) for n in range(15) for _ in range(2)]
    trades += [Trade(day(n), "ber", 1, (("lo", 1.0), ("ist", 4.0))) for n in range(15)]
    assert fit_runes(trades, SEED_RATES, until=day(14)).today()["ber"] == pytest.approx(
        12, rel=0.05
    )


def test_a_quiet_spell_widens_the_interval_and_the_future_counts():
    trades = [Trade(day(n), "lo", 1, (("ist", 6.0),)) for n in (*range(10), *range(25, 35))]
    fit = fit_runes(trades, SEED_RATES, until=day(34))
    width = {n: fit.at("lo", day(n)).high / fit.at("lo", day(n)).low for n in (5, 17, 30)}
    assert width[17] > width[5] and width[17] > width[30]


def test_a_rune_nobody_trades_keeps_its_seed_and_says_so():
    trades = [Trade(day(n), "lo", 1, (("ist", 6.0),)) for n in range(10)]
    zod = fit_runes(trades, SEED_RATES, until=day(9)).paths["zod"][-1]
    assert zod.value == pytest.approx(SEED_RATES["zod"], rel=0.01)
    assert zod.high / zod.low > 50  # A factor of 3 either way, at one standard deviation.


def test_bait_trades_are_down_weighted():
    rng = random.Random(11)
    trades = [sale(rng, n, "lo", 6.0, "ist", 1.0) for n in range(20) for _ in range(4)]
    trades += [Trade(day(n), "lo", 1, (("ist", 60.0),)) for n in range(0, 20, 4)]
    assert fit_runes(trades, SEED_RATES, until=day(19)).today()["lo"] == pytest.approx(6, rel=0.1)


def test_a_reset_lets_the_value_jump():
    trades = [Trade(day(n), "lo", 1, (("ist", 12.0),)) for n in range(20) for _ in range(2)]
    trades += [Trade(day(n), "lo", 1, (("ist", 5.0),)) for n in range(20, 26) for _ in range(2)]
    reset = fit_runes(trades, SEED_RATES, until=day(25), breaks=frozenset({day(20)}))
    smooth = fit_runes(trades, SEED_RATES, until=day(25))
    assert reset.at("lo", day(21)).value == pytest.approx(5, rel=0.1)
    assert abs(math.log(reset.at("lo", day(21)).value / 5)) < abs(
        math.log(smooth.at("lo", day(21)).value / 5)
    )


VALUES = {"ist": 1.0, "lo": 6.0, "ber": 12.0, "ohm": 4.0}


def test_a_sold_rune_is_worth_what_was_paid_per_rune():
    trade = Trade(day(0), "lo", 2, (("ohm", 3.0),))
    assert implied_value(trade, "lo", VALUES) == pytest.approx(6.0)


def test_a_payment_rune_is_worth_the_sale_less_the_rest_of_the_payment():
    """A Ber for one Lo and 4 Ist, Ber at 12: the Lo was worth 8."""
    trade = Trade(day(0), "ber", 1, (("lo", 1.0), ("ist", 4.0)))
    assert implied_value(trade, "lo", VALUES) == pytest.approx(8.0)


def test_a_payment_worth_more_than_the_sale_without_the_rune_implies_nothing():
    trade = Trade(day(0), "lo", 1, (("ber", 1.0), ("ist", 1.0)))
    assert implied_value(trade, "ist", VALUES) is None


def test_a_rune_not_in_the_trade_implies_nothing():
    assert implied_value(Trade(day(0), "lo", 1, (("ist", 6.0),)), "ber", VALUES) is None


def test_every_trade_leaves_evidence_for_each_rune_it_names():
    trades = [Trade(day(n), "lo", 1, (("ber", 0.5),)) for n in range(5)]
    trades += [Trade(day(n), "ber", 1, (("ist", 12.0),)) for n in range(5)]
    fit = fit_runes(trades, SEED_RATES, until=day(4))
    assert len(fit.observed["lo"]) == 5 and len(fit.observed["ber"]) == 10
    assert fit.observed["ist"] == []  # The unit needs no evidence.
    assert all(v == pytest.approx(6, rel=0.05) for _, v in fit.observed["lo"])


def test_the_same_trades_are_fitted_once():
    trades = [Trade(day(n), "lo", 1, (("ist", 6.0),)) for n in range(3)]
    assert fit_runes(trades, SEED_RATES, day(2)) is fit_runes(trades[::-1], SEED_RATES, day(2))


def test_the_interval_holds_a_wandering_web_of_runes():
    """Five runes on random walks, traded in pairs: the 95% interval holds the truth."""
    rng = random.Random(21)
    days = 40
    truth = {r: walk(rng, SEED_RATES[r] * rng.uniform(0.7, 1.4), days) for r in STATE_RUNES}
    truth["ist"] = [1.0] * days
    pairs = [("lo", "ist"), ("lo", "ohm"), ("ber", "lo"), ("ohm", "ist"), ("jah", "ber")]
    trades = []
    for n in range(days):
        for sold, paid in pairs:
            for _ in range(rng.randint(0, 2)):
                trades.append(sale(rng, n, sold, truth[sold][n], paid, truth[paid][n], 0.1))
    fit = fit_runes(trades, SEED_RATES, until=day(days - 1))
    held = [
        point.low <= truth[rune][n] <= point.high
        for rune in ("lo", "ohm", "ber", "jah")
        for n, point in enumerate(fit.paths[rune])
    ]
    assert sum(held) / len(held) >= 0.85
