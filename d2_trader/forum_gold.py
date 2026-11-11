"""What one forum gold is worth, measured through the runes both markets trade.

d2jsp prices runes in fg; Traderie's trades say what each rune is worth in Ist. So every
fg price of a rune is one noisy reading of Ist per fg: the rune's Ist value over the fg
it went for. All readings, from every rune and every day, feed one model of the rate as a
random walk with drift (`trend.py`): today's rate is yesterday's plus a step, the steps
lean one way for a while, and a reading far from the rest counts for less.

That means no reading is dropped, a day with none still has a rate (with an interval
that widens until prices arrive), and a price from any past day is valued at the rate
the model puts on that day, using what came before and after it.
"""

import bisect
import statistics
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from d2_trader.trend import fit_path

# The per-rune table on the Rates page describes this many recent days. The rate itself
# uses every reading.
WINDOW_DAYS = 7


@dataclass(frozen=True)
class FgQuote:
    rune: str
    fg: float  # Per one rune.
    at: datetime


@dataclass(frozen=True)
class RuneReading:
    rune: str
    fg_median: float
    ist_value: float
    quotes: int

    @property
    def ist_per_fg(self) -> float:
        return self.ist_value / self.fg_median


@dataclass(frozen=True)
class FgEstimate:
    """Ist per fg on one day, with its 95% interval and the readings made that day."""

    ist_per_fg: float
    low: float
    high: float
    observations: int


def readings(quotes: list[FgQuote], rune_values: Mapping[str, float]) -> list[RuneReading]:
    """One summary per rune that has both an fg price and an Ist value, for display."""
    by_rune: dict[str, list[float]] = defaultdict(list)
    for quote in quotes:
        if quote.fg > 0 and rune_values.get(quote.rune):
            by_rune[quote.rune].append(quote.fg)
    return [
        RuneReading(rune, statistics.median(prices), rune_values[rune], len(prices))
        for rune, prices in sorted(by_rune.items())
    ]


def fg_history(
    quotes: list[FgQuote],
    rune_values: Mapping[str, float],
    until: date,
    breaks: frozenset[date] = frozenset(),
) -> dict[date, FgEstimate]:
    """The rate on every day from the first reading to `until`.

    `breaks` are resets: days the market's Ist may jump against fg, as a new ladder's
    runes are scarce. The model is free to jump there instead of averaging across.
    """
    observations = [
        (q.at.date(), rune_values[q.rune] / q.fg)
        for q in quotes
        if q.fg > 0 and rune_values.get(q.rune)
    ]
    return {
        p.day: FgEstimate(p.value, p.low, p.high, p.observations)
        for p in fit_path(observations, until, breaks)
    }


def rate_on(history: Mapping[date, FgEstimate], day: date) -> FgEstimate | None:
    """The rate on `day`. Before the first reading, the earliest one is the best guess."""
    if not history:
        return None
    days = sorted(history)
    index = bisect.bisect_right(days, day)
    return history[days[max(index - 1, 0)]]


@dataclass(frozen=True)
class Valuation:
    """The rates one market is valued at, with forum gold allowed to vary over time."""

    rates: dict[str, float]
    fg_history: dict[date, FgEstimate] = field(default_factory=dict)

    def at(self, when: datetime) -> Mapping[str, float]:
        """Rates as they stood on a day: an fg price is read at the fg rate of its time."""
        found = rate_on(self.fg_history, when.date())
        return {**self.rates, "fg": found.ist_per_fg} if found else self.rates

    @property
    def current_fg(self) -> FgEstimate | None:
        return rate_on(self.fg_history, max(self.fg_history)) if self.fg_history else None
