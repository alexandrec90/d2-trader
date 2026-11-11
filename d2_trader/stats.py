"""From a pile of quotes to a price range, without being fooled by bait listings."""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from d2_trader.trend import PathPoint

MIN_SOLD_FOR_RANGE = 5
HIGH_CONFIDENCE_SOLD = 10
MIN_ASKS_FOR_RANGE = 5
# Below this a median absolute deviation says nothing, so nothing is called an outlier.
MIN_FOR_OUTLIERS = 4
OUTLIER_MADS = 3.5
# Prices cluster tightly, so without a floor a 1.6x sale would count as an outlier.
MIN_LOG_SPREAD = math.log(1.5)


@dataclass(frozen=True)
class Quote:
    kind: str
    value: float
    at: datetime
    source: str = ""


@dataclass(frozen=True)
class PriceRange:
    low: float
    median: float
    high: float
    samples: int
    outliers: int = 0


@dataclass(frozen=True)
class Summary:
    fair: PriceRange | None
    basis: str
    confidence: str
    lowest_ask: float | None
    median_ask: float | None
    highest_bid: float | None
    sold_median: float | None
    sold_count: int
    ask_count: int
    bid_count: int
    # The modelled price today, from every sale this season (see `trend.py`).
    estimate: PathPoint | None = None

    def metric(self, name: str) -> float | None:
        return {
            "estimate": self.estimate.value if self.estimate else None,
            "lowest_ask": self.lowest_ask,
            "median_ask": self.median_ask,
            "highest_bid": self.highest_bid,
            "sold_median": self.sold_median,
        }[name]


@dataclass(frozen=True)
class DayPoint:
    day: date
    sold_median: float | None
    sold_count: int
    ask_low: float | None
    ask_median: float | None
    ask_high: float | None


def drop_outliers(values: list[float]) -> list[float]:
    """Drop bait and joke prices. Prices scale, so distance is measured as a ratio."""
    if len(values) < MIN_FOR_OUTLIERS:
        return list(values)
    logs = [math.log(v) for v in values]
    center = statistics.median(logs)
    spread = max(statistics.median(abs(x - center) for x in logs) * 1.4826, MIN_LOG_SPREAD)
    return [
        v for v, x in zip(values, logs, strict=True) if abs(x - center) <= OUTLIER_MADS * spread
    ]


def price_range(values: list[float]) -> PriceRange | None:
    """Median and interquartile range of the values that survive outlier removal."""
    kept = sorted(drop_outliers(values))
    if not kept:
        return None
    if len(kept) == 1:
        return PriceRange(kept[0], kept[0], kept[0], 1, len(values) - 1)
    low, median, high = statistics.quantiles(kept, n=4, method="inclusive")
    return PriceRange(low, median, high, len(kept), len(values) - len(kept))


def _values(quotes: list[Quote], kind: str) -> list[float]:
    return [q.value for q in quotes if q.kind == kind and q.value > 0]


def summarize(quotes: list[Quote], estimate: PathPoint | None = None) -> Summary:
    """The market in one record. Sales outrank asks, because anyone can ask anything.

    `quotes` are the recent window; `estimate` comes from a longer view, every sale this
    season, so it is computed by the caller and carried here.
    """
    sold, asks, bids = (_values(quotes, k) for k in ("sold", "ask", "bid"))
    sold_range, ask_range = price_range(sold), price_range(asks)
    kept_asks = drop_outliers(asks)
    kept_bids = drop_outliers(bids)

    fair: PriceRange | None
    if sold_range and len(sold) >= MIN_SOLD_FOR_RANGE:
        fair, basis = sold_range, "sold"
        confidence = "high" if len(sold) >= HIGH_CONFIDENCE_SOLD else "medium"
    elif ask_range and len(asks) >= MIN_ASKS_FOR_RANGE:
        fair, basis, confidence = ask_range, "asking", "low"
    else:
        fair, basis, confidence = sold_range or ask_range, "thin", "none"

    return Summary(
        fair=fair,
        basis=basis,
        confidence=confidence,
        lowest_ask=min(kept_asks, default=None),
        median_ask=ask_range.median if ask_range else None,
        highest_bid=max(kept_bids, default=None),
        sold_median=sold_range.median if sold_range else None,
        sold_count=len(sold),
        ask_count=len(asks),
        bid_count=len(bids),
        estimate=estimate,
    )


def daily_history(quotes: list[Quote]) -> list[DayPoint]:
    """One point per day with data, for the chart. Days without quotes stay gaps."""
    days: dict[date, list[Quote]] = defaultdict(list)
    for quote in quotes:
        days[quote.at.date()].append(quote)
    points = []
    for day in sorted(days):
        sold = price_range(_values(days[day], "sold"))
        asks = price_range(_values(days[day], "ask"))
        points.append(
            DayPoint(
                day=day,
                sold_median=sold.median if sold else None,
                sold_count=sold.samples if sold else 0,
                ask_low=asks.low if asks else None,
                ask_median=asks.median if asks else None,
                ask_high=asks.high if asks else None,
            )
        )
    return points
