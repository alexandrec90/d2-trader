"""Every rune's value at once, from every rune-for-rune trade, as random walks.

A rune trade never says what a rune is worth in Ist. "Lo for 2 Ohm" and "Ohm for 4 Ist"
each relate two runes, so a Lo value has to be read off the whole web of trades: a Lo
sold for Ohm, a Ber sold for Lo, an Ohm sold for Ist all say something about Lo, the
last one without Lo in it at all. And today's values are yesterday's plus a step, so a
trade last week still says something about today, a little less than one today would.

So every rune's value relative to Ist is a hidden state, in log space because prices
scale, and all of them are estimated together:

    x[t] = x[t-1] + step                     step  ~ N(0, Q)
    0    = x[A] + log n - log(sum q_i e^x[c_i]) + noise,   one per trade

where a trade is n of rune A sold for q_1 of c_1 plus q_2 of c_2 and so on, and Ist is
the unit (x = 0). A trade in one kind of rune is linear in x; a mixed payment ("Lo + Ist")
is linearised at the current estimate, an extended Kalman filter. A Rauch-Tung-Striebel
smoother then carries later days back to earlier ones, so each day uses every trade.

Q = q^2 ((1 - rho) I + rho 11'): runes step independently, plus a shared part, because
Ist is the unit and itself moves, and when it does every ratio to it moves together.

What the rest of the project already does for one price (`trend.py`) holds here too:
an outlier is down-weighted by how surprising it is, never dropped; a day with no trades
widens the interval; a reset lets every value jump; and the noise levels are not assumed
but every plausible set on a grid is weighted by its likelihood and the results blended,
so doubt about them widens the interval. A rune nobody trades keeps its seed, with an
interval that says so.

How honest the 95% interval is, on simulated webs of runes on random walks (60 days,
seven trading pairs, 0-3 trades a pair a day, 12% noise; 20 runs): it held the true
value on 93% of rune-days on average (worst run 84%) with clean trades, and 89% (worst
70%) when one trade in twenty was a bait price of 0.1-10 times the truth.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

import numpy as np

from d2_trader.currency import BASE_CURRENCY, RUNES
from d2_trader.trend import HUBER_C, MIN_WEIGHT, Z_95, PathPoint

# Runes other than the unit: the state, in a fixed order.
STATE_RUNES = tuple(r for r in RUNES if r != BASE_CURRENCY)
INDEX = {rune: i for i, rune in enumerate(STATE_RUNES)}
# Before any trade, one standard deviation is a factor of 3: a seed is a guess.
PRIOR_VAR = math.log(3.0) ** 2
BREAK_VAR = math.log(2.0) ** 2  # A reset can double or halve a rune overnight.
# The grid weighed by likelihood. Daily steps from 1% (see trend.py for why no lower);
# the shared share of a step; and how far one trade strays from the market, in log units.
LEVEL_STEPS = (0.01, 0.02, 0.04, 0.08)
SHARED_SHARES = (0.0, 0.6)
# Spaced ~1.5x apart: at 2x, simulated intervals held the truth on 91% of days, not 93%.
TRADE_NOISE = (0.05, 0.08, 0.12, 0.18, 0.27, 0.4)


@dataclass(frozen=True, order=True)
class Trade:
    """`lot` of `rune` sold on `day` for every (currency, quantity) in `paid`, together."""

    day: date
    rune: str
    lot: float
    paid: tuple[tuple[str, float], ...]

    def involves(self) -> set[str]:
        return {self.rune, *(c for c, _ in self.paid)}


@dataclass(frozen=True)
class Candidate:
    step: float
    shared: float
    noise: float


@dataclass(frozen=True)
class RuneFit:
    """Each rune's daily value in Ist, with how many trades named it directly.

    `observed[rune]` is what each trade naming the rune says it was worth, in Ist, with
    the other side of the trade valued at the model's own estimate for that day: the
    evidence to draw beside the path.
    """

    paths: dict[str, list[PathPoint]]
    direct: dict[str, int]
    trades: int
    observed: dict[str, list[tuple[date, float]]] = field(default_factory=dict)

    def today(self) -> dict[str, float]:
        return {rune: path[-1].value for rune, path in self.paths.items() if path}

    def at(self, rune: str, day: date) -> PathPoint | None:
        return next((p for p in self.paths.get(rune, []) if p.day == day), None)


def trade_from(
    rune: str, day: date, quantity: int, alternatives: Sequence[Sequence[Sequence]]
) -> Trade | None:
    """A completed sale of a rune, if it says what was paid in runes and nothing else.

    A sale listing several alternatives does not say which one was paid, so it is left
    out, as is one paid partly in something that is not a rune, or in the rune itself.
    """
    if rune not in RUNES or len(alternatives) != 1 or not alternatives[0]:
        return None
    paid = tuple((str(c), float(q)) for c, q in alternatives[0])
    if any(c not in RUNES or c == rune or q <= 0 for c, q in paid):
        return None
    return Trade(day, rune, float(max(quantity, 1)), paid)


def process_noise(candidate: Candidate) -> np.ndarray:
    n = len(STATE_RUNES)
    shared = candidate.shared * np.ones((n, n))
    return candidate.step**2 * ((1 - candidate.shared) * np.eye(n) + shared)


def _linearise(trade: Trade, x: np.ndarray) -> tuple[float, np.ndarray]:
    """The trade's predicted log gap (0 when the estimate prices it exactly) and slope."""
    h = np.zeros(len(STATE_RUNES))
    logs = [math.log(q) + (x[INDEX[c]] if c in INDEX else 0.0) for c, q in trade.paid]
    top = max(logs)
    log_paid = top + math.log(sum(math.exp(v - top) for v in logs))
    for (currency, _), value in zip(trade.paid, logs, strict=True):
        if currency in INDEX:
            h[INDEX[currency]] -= math.exp(value - log_paid)
    sold = x[INDEX[trade.rune]] if trade.rune in INDEX else 0.0
    if trade.rune in INDEX:
        h[INDEX[trade.rune]] += 1.0
    return sold + math.log(trade.lot) - log_paid, h


def _update(x, cov, trade: Trade, noise_var: float):
    """Fold in one trade, down-weighted by how surprising it is. Returns x, cov, loglik."""
    predicted, h = _linearise(trade, x)
    surprise = -predicted
    ph = cov @ h
    belief = float(h @ ph)
    spread = belief + noise_var
    distance = abs(surprise) / math.sqrt(spread)
    if distance > HUBER_C:
        # As in trend.py: a bait trade counts as that much noisier, not as zero.
        spread = belief + noise_var * (distance / HUBER_C) ** 2
    gain = ph / spread
    x = x + gain * surprise
    cov = cov - np.outer(gain, ph)
    cov = (cov + cov.T) / 2
    loglik = -0.5 * (math.log(2 * math.pi * spread) + surprise**2 / spread)
    return x, cov, loglik


def _filter(by_day, days, prior, candidate: Candidate, breaks):
    q = process_noise(candidate)
    jump = BREAK_VAR * np.eye(len(STATE_RUNES))
    x, cov = prior.copy(), PRIOR_VAR * np.eye(len(STATE_RUNES))
    filtered, predicted, loglik = [], [], 0.0
    for i, day in enumerate(days):
        if i:
            cov = cov + q + (jump if day in breaks else 0.0)
        predicted.append(cov)
        for trade in by_day.get(day, ()):
            x, cov, term = _update(x, cov, trade, candidate.noise**2)
            loglik += term
        filtered.append((x, cov))
    return filtered, predicted, loglik


def _smooth(filtered, predicted):
    """Rauch-Tung-Striebel for a random walk: what later days learned, carried back."""
    x_s, cov_s = filtered[-1]
    out = [(x_s, cov_s)]
    for t in range(len(filtered) - 2, -1, -1):
        x_f, cov_f = filtered[t]
        gain = np.linalg.solve(predicted[t + 1].T, cov_f.T).T  # cov_f @ inv(predicted)
        x_s = x_f + gain @ (x_s - x_f)
        cov_s = cov_f + gain @ (cov_s - predicted[t + 1]) @ gain.T
        out.append((x_s, cov_s))
    return out[::-1]


def grid() -> list[Candidate]:
    return [
        Candidate(step, shared, noise)
        for step in LEVEL_STEPS
        for shared in SHARED_SHARES
        for noise in TRADE_NOISE
    ]


def weigh(scores: list[float]) -> list[float]:
    """Likelihoods to weights summing to one, dropping any too small to matter."""
    top = max(scores)
    raw = [math.exp(s - top) for s in scores]
    total = sum(raw)
    kept = [w if w / total >= MIN_WEIGHT else 0.0 for w in raw]
    return [w / sum(kept) for w in kept]


def implied_value(trade: Trade, rune: str, values: Mapping[str, float]) -> float | None:
    """What `trade` says one `rune` was worth, the rest of it valued at `values`.

    None when the rest is worth as much as the whole, so the trade says nothing sensible.
    """
    paid = sum(q * values[c] for c, q in trade.paid)
    if rune == trade.rune:
        return paid / trade.lot
    quantity = sum(q for c, q in trade.paid if c == rune)
    if not quantity:
        return None
    rest = paid - quantity * values[rune]
    implied = (trade.lot * values[trade.rune] - rest) / quantity
    return implied if implied > 0 else None


def fit_runes(
    trades: Iterable[Trade],
    seeds: Mapping[str, float],
    until: date,
    start: date | None = None,
    breaks: frozenset[date] = frozenset(),
) -> RuneFit:
    """Every rune's value in Ist on every day from `start` (or the first trade) to `until`.

    `seeds` are the values believed before any trade, in Ist, for every rune. A page
    redraws live, so the same inputs are only ever fitted once.
    """
    kept = tuple(sorted(t for t in trades if t.day <= until and (start is None or t.day >= start)))
    seeded = tuple(sorted((r, seeds[r]) for r in STATE_RUNES))
    return _fit_cached(kept, seeded, until, start, breaks)


@lru_cache(maxsize=32)
def _fit_cached(
    kept: tuple[Trade, ...],
    seeded: tuple[tuple[str, float], ...],
    until: date,
    start: date | None,
    breaks: frozenset[date],
) -> RuneFit:
    seeds = dict(seeded)
    if not kept:
        return RuneFit({}, {}, 0)
    first = start or min(t.day for t in kept)
    days = [first + timedelta(days=i) for i in range((until - first).days + 1)]
    by_day: dict[date, list[Trade]] = {}
    for trade in kept:
        by_day.setdefault(trade.day, []).append(trade)
    prior = np.array([math.log(seeds[r]) for r in STATE_RUNES])

    runs = [_filter(by_day, days, prior, c, breaks) for c in grid()]
    mean, spread = _blend(runs, len(days))
    paths = _paths(days, by_day, mean, spread)
    return RuneFit(paths, _direct(kept), len(kept), _observed(kept, paths, first))


def _blend(runs, n_days: int) -> tuple[np.ndarray, np.ndarray]:
    """The likelihood-weighted mean and spread of every grid run's smoothed states."""
    weights = weigh([run[2] for run in runs])
    mean = np.zeros((n_days, len(STATE_RUNES)))
    second = np.zeros_like(mean)
    for weight, (filtered, predicted, _) in zip(weights, runs, strict=True):
        if not weight:
            continue
        for i, (x, cov) in enumerate(_smooth(filtered, predicted)):
            mean[i] += weight * x
            second[i] += weight * (np.clip(np.diag(cov), 0, None) + x**2)
    return mean, np.sqrt(np.clip(second - mean**2, 0, None))


def _direct(kept: Sequence[Trade]) -> dict[str, int]:
    """How many trades name each rune."""
    direct = {r: 0 for r in RUNES}
    for trade in kept:
        for rune in trade.involves():
            direct[rune] += 1
    return direct


def _paths(days, by_day, mean: np.ndarray, spread: np.ndarray) -> dict[str, list[PathPoint]]:
    """Each rune's daily value and 95% interval in Ist; Ist itself is 1 throughout."""
    paths = {BASE_CURRENCY: [PathPoint(d, 1.0, 1.0, 1.0, len(by_day.get(d, ()))) for d in days]}
    for j, rune in enumerate(STATE_RUNES):
        paths[rune] = [
            PathPoint(
                day=d,
                value=math.exp(mean[i, j]),
                low=math.exp(mean[i, j] - Z_95 * spread[i, j]),
                high=math.exp(mean[i, j] + Z_95 * spread[i, j]),
                observations=sum(rune in t.involves() for t in by_day.get(d, ())),
            )
            for i, d in enumerate(days)
        ]
    return paths


def _observed(kept, paths, first: date) -> dict[str, list[tuple[date, float]]]:
    """Each trade's implied Ist value for every non-Ist rune in it, at that day's values."""
    observed: dict[str, list[tuple[date, float]]] = {r: [] for r in RUNES}
    for trade in kept:
        values = {r: paths[r][(trade.day - first).days].value for r in trade.involves()}
        for rune in trade.involves() - {BASE_CURRENCY}:
            implied = implied_value(trade, rune, values)
            if implied is not None:
                observed[rune].append((trade.day, implied))
    return observed
