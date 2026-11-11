"""A price that wanders: a robust local linear trend, filtered and then smoothed.

Prices are not independent from one day to the next. Today's is yesterday's plus a step,
and the steps lean one way for a while (fg loses value through a season, launch prices
fall). So a price is modelled as a hidden level that moves as a random walk with drift,
in log space because prices scale:

    level[t] = level[t-1] + drift[t-1] + step         step   ~ N(0, q_level)
    drift[t] = drift[t-1] + nudge                     nudge  ~ N(0, q_drift)
    quote    = level[t] + noise                       noise  ~ N(0, r)

A Kalman filter runs forward through the days, and a Rauch-Tung-Striebel smoother runs
back, so every day's estimate uses every quote, before and after it. What that buys:

- A day with no quotes still has an estimate. Its interval simply widens with the gap.
- An outlier is down-weighted, never thrown away: a quote far from where the level is
  expected counts for less, the further the less.
- The noise levels are not assumed. Every plausible set is weighted by how well it
  explains the data and the estimates are blended, so a series with no real drift comes
  out with almost none, and doubt about the noise widens the interval.

How honest the 95% interval is, measured on simulated random walks with drift (120 days,
0-4 quotes a day): it held the true price on 92-99% of days with clean quotes, and on
86-98% with one quote in twenty a bait or joke price of 3-12 times the truth.
- A reset is a break: the level may jump there, and the model learns the new one fast,
  without pretending the old season never happened.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

Z_95 = 1.96
# Huber's threshold, in standard deviations of the surprise: past it, a quote's weight
# falls off as 1/distance rather than counting in full.
HUBER_C = 2.5
INITIAL_LEVEL_VAR = 1.0  # A level of e^±2 either way before any quote: no real prior.
INITIAL_DRIFT_VAR = 0.05**2
BREAK_LEVEL_VAR = math.log(2.0) ** 2  # A reset can double or halve a price overnight.
BREAK_DRIFT_VAR = 0.02**2
DEFAULT_NOISE_VAR = 0.15**2  # Used until enough same-day quotes say otherwise.
MIN_NOISE_VAR = 0.02**2
# Candidate daily standard deviations for the level's steps and the drift's nudges.
# None below 1% a day, on purpose. On a few months of quotes the likelihood readily
# prefers a level that barely moves (the "pile-up" of random-walk variance estimates at
# zero), and that was the whole of the shortfall in simulation: with 0.2% and 0.5% in the
# grid, a 95% interval held the true price on 89% of days. A D2 price that moves less
# than 1% a day just gets an interval a little wider than it needs, the safe side.
LEVEL_STEPS = (0.01, 0.02, 0.04, 0.08)
DRIFT_NUDGES = (0.0, 0.0005, 0.002, 0.005)
# Multiples of the same-day scatter tried as the quote noise.
QUOTE_SCALES = (0.75, 1.0, 1.5, 2.25, 3.4)
# A candidate this unlikely changes nothing visible, so it is not smoothed.
MIN_WEIGHT = 1e-3

Vector = tuple[float, float]
Matrix = tuple[float, float, float, float]  # Row-major 2x2.


@dataclass(frozen=True)
class PathPoint:
    """The estimated price on one day, with its 95% interval, in the quotes' own units."""

    day: date
    value: float
    low: float
    high: float
    observations: int


@dataclass(frozen=True)
class Noise:
    level: float
    drift: float
    quote: float


@dataclass(frozen=True)
class _Day:
    state: Vector
    cov: Matrix
    predicted_state: Vector
    predicted_cov: Matrix
    observations: int


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
    )


def _transpose(m: Matrix) -> Matrix:
    return (m[0], m[2], m[1], m[3])


def _inverse(m: Matrix) -> Matrix:
    det = m[0] * m[3] - m[1] * m[2]
    return (m[3] / det, -m[1] / det, -m[2] / det, m[0] / det)


def _add(a: Matrix, b: Matrix) -> Matrix:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


def _sub(a: Matrix, b: Matrix) -> Matrix:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2], a[3] - b[3])


TRANSITION: Matrix = (1.0, 1.0, 0.0, 1.0)


def noise_variance(by_day: dict[date, list[float]]) -> float:
    """How far quotes on one day scatter around each other, robustly (MAD)."""
    residuals = []
    for values in by_day.values():
        if len(values) >= 2:
            center = statistics.median(values)
            residuals += [v - center for v in values]
    if len(residuals) < 3:
        return DEFAULT_NOISE_VAR
    spread = statistics.median(abs(x) for x in residuals) * 1.4826
    return max(spread**2, MIN_NOISE_VAR)


def _predict(state: Vector, cov: Matrix, noise: Noise, is_break: bool) -> tuple[Vector, Matrix]:
    a, b, _, c = cov
    level_var = a + 2 * b + c + noise.level
    drift_var = c + noise.drift
    if is_break:
        level_var += BREAK_LEVEL_VAR
        drift_var += BREAK_DRIFT_VAR
    return (state[0] + state[1], state[1]), (level_var, b + c, b + c, drift_var)


def _update(state: Vector, cov: Matrix, quote: float, noise: Noise) -> tuple[Vector, Matrix, float]:
    """Fold in one quote. Returns the new state, covariance and log-likelihood term."""
    a, b, _, c = cov
    surprise = quote - state[0]
    spread = a + noise.quote
    distance = abs(surprise) / math.sqrt(spread)
    if distance > HUBER_C:
        # A quote this surprising is treated as that much noisier, not discarded: by the
        # square of the distance, so a bait price ten times too low pulls less than one
        # three times too low. A cap on the pull alone (Huber) let frequent bait add up.
        spread = a + noise.quote * (distance / HUBER_C) ** 2
    gain = (a / spread, b / spread)
    state = (state[0] + gain[0] * surprise, state[1] + gain[1] * surprise)
    b_new = b - gain[0] * b
    cov = (a - gain[0] * a, b_new, b_new, c - gain[1] * b)
    loglik = -0.5 * (math.log(2 * math.pi * spread) + surprise**2 / spread)
    return state, cov, loglik


def _filter(
    by_day: dict[date, list[float]], start: date, until: date, noise: Noise, breaks: set[date]
) -> tuple[list[_Day], float]:
    first = by_day[start]
    state: Vector = (statistics.median(first), 0.0)
    cov: Matrix = (INITIAL_LEVEL_VAR, 0.0, 0.0, INITIAL_DRIFT_VAR)
    days: list[_Day] = []
    loglik = 0.0
    day = start
    while day <= until:
        if day != start:
            state, cov = _predict(state, cov, noise, day in breaks)
        predicted = (state, cov)
        for quote in by_day.get(day, []):
            state, cov, term = _update(state, cov, quote, noise)
            loglik += term
        days.append(_Day(state, cov, *predicted, len(by_day.get(day, []))))
        day += timedelta(days=1)
    return days, loglik


def _smooth(days: list[_Day]) -> list[tuple[Vector, Matrix]]:
    """Rauch-Tung-Striebel: carry what later days learned back to earlier ones."""
    smoothed = [(days[-1].state, days[-1].cov)]
    for today, tomorrow in zip(reversed(days[:-1]), reversed(days[1:]), strict=True):
        later_state, later_cov = smoothed[-1]
        gain = _mul(_mul(today.cov, _transpose(TRANSITION)), _inverse(tomorrow.predicted_cov))
        diff = (
            later_state[0] - tomorrow.predicted_state[0],
            later_state[1] - tomorrow.predicted_state[1],
        )
        state = (
            today.state[0] + gain[0] * diff[0] + gain[1] * diff[1],
            today.state[1] + gain[2] * diff[0] + gain[3] * diff[1],
        )
        spread = _mul(_mul(gain, _sub(later_cov, tomorrow.predicted_cov)), _transpose(gain))
        smoothed.append((state, _add(today.cov, spread)))
    return smoothed[::-1]


def weigh_noise(
    by_day: dict[date, list[float]], start: date, until: date, breaks: set[date]
) -> list[tuple[float, Noise]]:
    """Every plausible set of noise levels, weighted by how well it explains the quotes.

    Picking the single most likely set and treating it as known makes the interval too
    narrow: with a few months of data the noise levels are themselves uncertain. In a
    simulation of known random walks, the best fit alone covered the truth 88% of days
    where its interval claimed 95%. Averaging over them, each weighted by its likelihood
    (a flat prior over the grid), puts that uncertainty back.
    """
    base = noise_variance(by_day)
    grid = [
        Noise(step**2, nudge**2, base * scale)
        for step in LEVEL_STEPS
        for nudge in DRIFT_NUDGES
        for scale in QUOTE_SCALES
    ]
    scored = [(_filter(by_day, start, until, n, breaks)[1], n) for n in grid]
    top = max(score for score, _ in scored)
    weights = [(math.exp(score - top), n) for score, n in scored]
    total = sum(w for w, _ in weights)
    kept = [(w, n) for w, n in weights if w / total >= MIN_WEIGHT]
    # Weights must sum to one again, or the blend is pulled towards zero.
    kept_total = sum(w for w, _ in kept)
    return [(w / kept_total, n) for w, n in kept]


def fit_path(
    quotes: list[tuple[date, float]], until: date, breaks: frozenset[date] = frozenset()
) -> list[PathPoint]:
    """The estimated price for every day from the first quote to `until`.

    `quotes` are (day, price) pairs, any number per day, none on some. Prices must be
    positive. `breaks` are days on which the price may jump, such as a ladder reset.
    A fit takes tens of milliseconds and pages redraw live, so the same inputs are only
    ever fitted once.
    """
    return list(_fit_cached(tuple(sorted(quotes)), until, breaks))


@lru_cache(maxsize=512)
def _fit_cached(
    quotes: tuple[tuple[date, float], ...], until: date, breaks: frozenset[date]
) -> tuple[PathPoint, ...]:
    by_day: dict[date, list[float]] = defaultdict(list)
    for day, price in quotes:
        if price > 0 and day <= until:
            by_day[day].append(math.log(price))
    if not by_day:
        return ()
    start = min(by_day)
    count = (until - start).days + 1
    mean, second = [0.0] * count, [0.0] * count
    observed = [len(by_day.get(start + timedelta(days=i), [])) for i in range(count)]
    for weight, noise in weigh_noise(by_day, start, until, set(breaks)):
        days, _ = _filter(by_day, start, until, noise, set(breaks))
        for i, (state, cov) in enumerate(_smooth(days)):
            mean[i] += weight * state[0]
            second[i] += weight * (max(cov[0], 0.0) + state[0] ** 2)
    points = []
    for i in range(count):
        # The blend's variance: each candidate's own, plus how far they disagree.
        margin = Z_95 * math.sqrt(max(second[i] - mean[i] ** 2, 0.0))
        points.append(
            PathPoint(
                day=start + timedelta(days=i),
                value=math.exp(mean[i]),
                low=math.exp(mean[i] - margin),
                high=math.exp(mean[i] + margin),
                observations=observed[i],
            )
        )
    return tuple(points)
