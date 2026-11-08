"""The price model, checked against series whose true price is known."""

import math
import random
from datetime import date, timedelta

import pytest

from d2_trader.trend import (
    DEFAULT_NOISE_VAR,
    LEVEL_STEPS,
    MIN_NOISE_VAR,
    Noise,
    PathPoint,
    _fit_cached,
    fit_path,
    noise_variance,
    weigh_noise,
)

START = date(2026, 9, 1)


def day(n: int) -> date:
    return START + timedelta(days=n)


def by_point(path: list[PathPoint]) -> dict[date, PathPoint]:
    return {p.day: p for p in path}


def test_no_quotes_no_path():
    assert fit_path([], until=day(10)) == []


def test_a_flat_price_is_found_and_bracketed():
    rng = random.Random(1)
    quotes = [(day(d), 100 * rng.uniform(0.9, 1.1)) for d in range(30) for _ in range(3)]
    last = fit_path(quotes, until=day(29))[-1]
    assert last.value == pytest.approx(100, rel=0.05)
    assert last.low < 100 < last.high
    assert last.high / last.low < 1.25  # Ninety quotes make for a tight interval.


def test_every_day_up_to_until_gets_a_point():
    path = fit_path([(day(0), 10.0), (day(3), 10.0)], until=day(6))
    assert [p.day for p in path] == [day(n) for n in range(7)]
    assert [p.observations for p in path] == [1, 0, 0, 1, 0, 0, 0]


def test_a_quiet_spell_widens_the_interval_and_data_narrows_it_again():
    quotes = [(day(d), 50.0) for d in range(10) for _ in range(2)]
    quotes += [(day(d), 50.0) for d in range(25, 35) for _ in range(2)]
    points = by_point(fit_path(quotes, until=day(34)))
    width = {d: points[day(d)].high / points[day(d)].low for d in (5, 17, 30)}
    assert width[17] > width[5]
    assert width[17] > width[30]


def test_the_future_counts_when_valuing_the_past():
    """A quiet day between two prices is valued between them, not at the earlier one."""
    quotes = [(day(d), 10.0) for d in range(5) for _ in range(3)]
    quotes += [(day(d), 20.0) for d in range(10, 15) for _ in range(3)]
    middle = by_point(fit_path(quotes, until=day(14)))[day(7)]
    assert 10.5 < middle.value < 19.5


def test_an_outlier_counts_for_little_but_is_not_thrown_away():
    rng = random.Random(2)
    quotes = [(day(d), 100 * rng.uniform(0.95, 1.05)) for d in range(20) for _ in range(2)]
    plain = by_point(fit_path(quotes, until=day(19)))[day(10)].value
    with_bait = by_point(fit_path([*quotes, (day(10), 1000.0)], until=day(19)))[day(10)].value
    assert with_bait == pytest.approx(plain, rel=0.1)  # Ten times the price moved it < 10%.
    assert with_bait > plain  # It was still counted.


def test_a_steady_drift_is_followed():
    quotes = [(day(d), 10 * 1.02**d) for d in range(40) for _ in range(2)]
    last = fit_path(quotes, until=day(39))[-1]
    assert last.value == pytest.approx(10 * 1.02**39, rel=0.05)


def test_a_reset_lets_the_price_jump():
    quotes = [(day(d), 10.0) for d in range(20) for _ in range(2)]
    quotes += [(day(d), 30.0) for d in range(20, 24) for _ in range(2)]
    with_break = fit_path(quotes, until=day(23), breaks=frozenset({day(20)}))[-1]
    without = fit_path(quotes, until=day(23))[-1]
    assert with_break.value == pytest.approx(30, rel=0.1)
    assert abs(with_break.value - 30) <= abs(without.value - 30)


def test_the_interval_covers_a_random_walk_most_of_the_time():
    """The honest test of a 95% band: simulate truth, see how often the band holds it."""
    rng = random.Random(3)
    level, drift, quotes, truth = math.log(100), 0.003, [], {}
    for d in range(90):
        level += drift + rng.gauss(0, 0.02)
        truth[day(d)] = math.exp(level)
        for _ in range(rng.choice([0, 1, 2, 3])):  # Some days have no trades at all.
            quotes.append((day(d), math.exp(level + rng.gauss(0, 0.1))))
    path = fit_path(quotes, until=day(89))
    first = path[0].day
    held = [p.low <= truth[p.day] <= p.high for p in path if p.day >= first]
    assert sum(held) / len(held) >= 0.9


def test_a_quote_in_the_future_is_ignored():
    path = fit_path([(day(0), 5.0), (day(9), 50.0)], until=day(3))
    assert len(path) == 4
    assert path[-1].value == pytest.approx(5.0, rel=0.01)


def test_free_or_negative_prices_are_not_quotes():
    assert fit_path([(day(0), 0.0), (day(1), -3.0)], until=day(2)) == []


def test_quote_noise_is_measured_from_same_day_scatter():
    by_day = {day(0): [0.0, 0.2, -0.2], day(1): [1.0, 1.2, 0.8]}
    assert noise_variance(by_day) == pytest.approx((0.2 * 1.4826) ** 2)


def test_quote_noise_has_a_default_and_a_floor():
    assert noise_variance({day(0): [1.0]}) == DEFAULT_NOISE_VAR
    assert noise_variance({day(0): [1.0, 1.0], day(1): [2.0, 2.0]}) == MIN_NOISE_VAR


def weighted_step(weights: list[tuple[float, Noise]]) -> float:
    return sum(w * math.sqrt(n.level) for w, n in weights)


def test_a_calm_series_is_weighted_to_small_steps_and_a_wild_one_to_big_ones():
    rng = random.Random(4)
    calm = {day(d): [math.log(100) + rng.gauss(0, 0.05) for _ in range(3)] for d in range(40)}
    level, wild = math.log(100), {}
    for d in range(40):
        level += rng.gauss(0, 0.08)
        wild[day(d)] = [level + rng.gauss(0, 0.05) for _ in range(3)]
    calm_weights = weigh_noise(calm, day(0), day(39), set())
    wild_weights = weigh_noise(wild, day(0), day(39), set())
    assert weighted_step(calm_weights) < weighted_step(wild_weights)


def test_noise_weights_sum_to_one_after_dropping_the_negligible():
    rng = random.Random(5)
    by_day = {day(d): [rng.gauss(0, 0.1) for _ in range(2)] for d in range(30)}
    weights = weigh_noise(by_day, day(0), day(29), set())
    assert sum(w for w, _ in weights) == pytest.approx(1.0)


def test_no_candidate_believes_a_price_almost_never_moves():
    # On a few months of data, the likelihood alone piles weight onto near-zero steps.
    assert min(LEVEL_STEPS) >= 0.01


def test_a_far_outlier_pulls_less_than_a_near_one():
    rng = random.Random(6)
    quotes = [(day(d), 100 * rng.uniform(0.97, 1.03)) for d in range(20) for _ in range(2)]
    base = by_point(fit_path(quotes, until=day(19)))[day(10)].value
    near = by_point(fit_path([*quotes, (day(10), 300.0)], until=day(19)))[day(10)].value
    far = by_point(fit_path([*quotes, (day(10), 3000.0)], until=day(19)))[day(10)].value
    assert far - base < near - base


def test_the_same_inputs_are_fitted_once():
    quotes = [(day(0), 10.0), (day(1), 11.0)]
    assert fit_path(quotes, until=day(2)) == fit_path(list(reversed(quotes)), until=day(2))
    assert _fit_cached.cache_info().hits >= 1
