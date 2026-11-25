"""What gems, shards, keys and other bulk currencies are worth, measured through runes.

Each trade of a good against runes is one reading of its value in Ist: "Ist for 20 Deep
Worldstone Shards" says 0.05 a shard, at the rune model's values for that day. All of a
good's readings feed one price model (`trend.py`), as forum gold's do.

Goods are fitted after the runes rather than with them. A rune's value comes from far
more trades than any good's, so a good has little to teach it, and goods trades (bulk
discounts, mistyped lots) could only blur it.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from d2_trader.currency import GOODS, MIN_RATE_SAMPLES, RUNES
from d2_trader.trend import PathPoint, fit_path

# A busy good trades hundreds of times a day, which adds cost and no accuracy: the fit
# takes this many of a day's readings, evenly spaced from its cheapest to its dearest.
READINGS_PER_DAY = 12

Values = Mapping[str, float]


@dataclass(frozen=True)
class Sale:
    """A completed sale: `quantity` of `item` for any one of `alternatives`."""

    item: str
    day: date
    quantity: int
    alternatives: Sequence[Sequence[Sequence]]


@dataclass(frozen=True)
class GoodsFit:
    """Each traded good's daily value in Ist, its readings, and how many there were."""

    paths: dict[str, list[PathPoint]] = field(default_factory=dict)
    observed: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    direct: dict[str, int] = field(default_factory=dict)

    def today(self) -> dict[str, float]:
        return {good: path[-1].value for good, path in self.paths.items()}

    def measured(self) -> dict[str, float]:
        """Today's value of each good with enough trades to value other listings by."""
        today = self.today()
        return {g: v for g, v in today.items() if self.direct[g] >= MIN_RATE_SAMPLES}


def reading(sale: Sale, values: Values) -> tuple[str, float] | None:
    """The good a sale prices and what it says one is worth in Ist, if it says so plainly.

    Either a good sold for runes alone, or a rune sold for one kind of good alone. A mixed
    payment ("Lem + 5 Random Gems") leaves the good as a small remainder, mostly the noise
    of the rest. Several alternatives do not say which one was paid.
    """
    if len(sale.alternatives) != 1 or not sale.alternatives[0]:
        return None
    paid = [(str(c), float(q)) for c, q in sale.alternatives[0]]
    if any(q <= 0 for _, q in paid):
        return None
    lot = max(sale.quantity, 1)
    if sale.item in GOODS:
        if any(c not in RUNES or c not in values for c, _ in paid):
            return None
        return sale.item, sum(q * values[c] for c, q in paid) / lot
    if len(paid) == 1 and paid[0][0] in GOODS and sale.item in values:
        good, quantity = paid[0]
        return good, lot * values[sale.item] / quantity
    return None


def thin(readings: Iterable[tuple[date, float]]) -> list[tuple[date, float]]:
    """At most `READINGS_PER_DAY` of each day's readings, spanning its whole spread."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for day, value in readings:
        by_day[day].append(value)
    kept = []
    for day, found in by_day.items():
        ordered = sorted(found)
        if len(ordered) > READINGS_PER_DAY:
            # The middle of each of READINGS_PER_DAY equal slices.
            width = len(ordered) / READINGS_PER_DAY
            ordered = [ordered[int((i + 0.5) * width)] for i in range(READINGS_PER_DAY)]
        kept += [(day, value) for value in ordered]
    return kept


def fit_goods(
    sales: Iterable[Sale],
    rune_paths: Mapping[str, Sequence[PathPoint]],
    until: date,
    breaks: frozenset[date] = frozenset(),
) -> GoodsFit:
    """Every traded good's value in Ist on every day from its first trade to `until`."""
    values: dict[date, dict[str, float]] = defaultdict(dict)
    for rune, path in rune_paths.items():
        for point in path:
            values[point.day][rune] = point.value
    observed: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for sale in sales:
        found = reading(sale, values.get(sale.day, {}))
        if found is not None:
            observed[found[0]].append((sale.day, found[1]))
    ordered = {good: observed[good] for good in GOODS if good in observed}
    paths = {good: fit_path(thin(r), until, breaks) for good, r in ordered.items()}
    return GoodsFit(paths, ordered, {good: len(r) for good, r in ordered.items()})
