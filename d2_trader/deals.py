"""Is a trade a good deal? Both sides valued in Ist, with how sure the answer is."""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from d2_trader.currency import GOODS, RUNES, display_name, normalize_currency

Lots = list[tuple[str, float]]

# Closer than this is a fair trade, however sure the values are.
FAIR_MARGIN = 0.05
SEPARATORS = re.compile(r"\s*(?:\+|,|&|\band\b)\s*")
# "20 deep shards", "20x deep shards", "20ist"; the name may itself start with a digit.
COUNTED = re.compile(r"(\d+(?:\.\d+)?)(?:\s*x\s+|\s+|(?=[a-z]))(.+)")


class UnreadableDeal(ValueError):
    """Something typed that is not a currency, or that no trade has valued yet."""


@dataclass(frozen=True)
class Estimate:
    """A currency's value in Ist today, with its 95% interval."""

    value: float
    low: float
    high: float


@dataclass(frozen=True)
class Deal:
    give: Lots
    get: Lots
    give_ist: float
    get_ist: float
    ratio: float  # What you get over what you give.
    low: float
    high: float
    verdict: str  # "good", "bad", "fair" or "unclear".
    # How much of the one currency given would be exactly fair. None for a bundle.
    fair_give: float | None


def _words(name: str) -> frozenset[str]:
    """A name's words, singular, without the "rune" that a rune may or may not carry."""
    return frozenset(w.removesuffix("s") for w in name.split()) - {"rune"}


def resolve(name: str) -> str:
    """The currency a person means: 'amethysts', 'deep shard' and 'Ist runes' all work."""
    key = normalize_currency(name)
    known = (*RUNES, *GOODS)
    if key in known:
        return key
    wanted = _words(key)
    exact = [c for c in known if _words(c) == wanted]
    found = exact or [c for c in known if wanted and wanted <= _words(c)]
    if not found:
        raise UnreadableDeal(f"No currency called '{name}'")
    if len(found) > 1:
        options = ", ".join(display_name(c) for c in found)
        raise UnreadableDeal(f"'{name}' could be {options}: say which")
    return found[0]


def parse_lot(text: str) -> tuple[str, float]:
    text = re.sub(r"^an?\s+", "", text)
    try:
        return resolve(text), 1.0  # First, because "3x3 Key Set" starts with a number.
    except UnreadableDeal:
        counted = COUNTED.fullmatch(text)
        if counted is None:
            raise
    quantity = float(counted[1])
    if quantity <= 0:
        raise UnreadableDeal(f"'{text}' is none at all")
    return resolve(counted[2]), quantity


def parse_side(text: str) -> Lots:
    """'20 deep shards + 1 pul' as lots."""
    parts = [p for p in SEPARATORS.split(" ".join(text.lower().split())) if p]
    if not parts:
        raise UnreadableDeal("Say what is on both sides of the trade")
    return [parse_lot(p) for p in parts]


def _side(lots: Iterable[tuple[str, float]], values: Mapping[str, Estimate]) -> tuple[float, float]:
    """A side's value in Ist, and the half-width of its 95% interval in log units."""
    worth = []
    for currency, quantity in lots:
        if currency not in values:
            raise UnreadableDeal(f"Too few trades to value {display_name(currency)} yet")
        estimate = values[currency]
        worth.append((quantity * estimate.value, math.log(estimate.high / estimate.value)))
    total = sum(v for v, _ in worth)
    return total, math.sqrt(sum((v / total * margin) ** 2 for v, margin in worth))


def judge(give: Lots, get: Lots, values: Mapping[str, Estimate]) -> Deal:
    """What a trade is worth to the person making it."""
    give_ist, give_margin = _side(give, values)
    get_ist, get_margin = _side(get, values)
    ratio = get_ist / give_ist
    margin = math.exp(math.hypot(give_margin, get_margin))
    low, high = ratio / margin, ratio * margin
    if abs(ratio - 1) < FAIR_MARGIN:
        verdict = "fair"
    elif low > 1 or high < 1:
        verdict = "good" if ratio > 1 else "bad"
    else:
        verdict = "unclear"
    fair_give = get_ist / values[give[0][0]].value if len(give) == 1 else None
    return Deal(give, get, give_ist, get_ist, ratio, low, high, verdict, fair_give)
