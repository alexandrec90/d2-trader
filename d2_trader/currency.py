"""One unit for every price. D2R has no money, so everything is valued in Ist runes.

A listing's price is a list of alternatives, any one of which the seller accepts; each
alternative is a list of (currency, quantity) lots that are all paid together.
"""

import statistics
from collections.abc import Iterable, Mapping

Alternative = list[tuple[str, float]]

BASE_CURRENCY = "ist"

# Measured from Traderie's softcore ladder RotW trades on 2026-11-06, late in Season 15,
# for Pul and up. Below Pul, and for keys and gems, these are rough: they rarely move a
# price, and refresh_rates or the Rates page replaces any of them. `fg` is a placeholder:
# nothing measures it, so set it on the Rates page before trusting a d2jsp import.
SEED_RATES: dict[str, float] = {
    **dict.fromkeys("el eld tir nef eth ith tal ral ort thul amn sol".split(), 0.01),
    "shael": 0.02,
    "dol": 0.03,
    **dict.fromkeys("hel io lum ko".split(), 0.05),
    "fal": 0.08,
    "lem": 0.15,
    "pul": 0.33,
    "um": 0.5,
    "mal": 0.67,
    "ist": 1.0,
    "gul": 1.5,
    "vex": 2.5,
    "ohm": 4.0,
    "lo": 6.0,
    "sur": 8.0,
    "ber": 12.5,
    "jah": 17.0,
    "cham": 12.5,
    "zod": 22.0,
    **dict.fromkeys(
        (f"perfect {gem}" for gem in "amethyst diamond emerald ruby sapphire skull topaz".split()),
        0.03,
    ),
    **dict.fromkeys(("key of terror", "key of hate", "key of destruction"), 0.05),
    "fg": 0.1,
}

RUNES = (
    "el eld tir nef eth ith tal ral ort thul amn sol shael dol hel io lum ko fal lem pul um "
    "mal ist gul vex ohm lo sur ber jah cham zod"
).split()

# What a price shows when there is none.
NO_VALUE = "\N{EN DASH}"
DISPLAY_UNITS = ("zod", "ber", "lo", "ist")
MIN_RATE_SAMPLES = 5
MAX_RATE_DRIFT = 5.0


def normalize_currency(name: str) -> str:
    """'Ber Rune' and 'Ber' are one currency, and so are 'FG' and 'forum gold'."""
    key = " ".join(name.lower().split())
    if key in ("forum gold", "forumgold"):
        return "fg"
    return key.removesuffix(" rune")


def alternative_value(lots: Iterable[tuple[str, float]], rates: Mapping[str, float]):
    total = 0.0
    for currency, quantity in lots:
        rate = rates.get(currency)
        if rate is None:
            return None
        total += quantity * rate
    return total


def unit_value(
    alternatives: list[Alternative],
    rates: Mapping[str, float],
    kind: str,
    lot_size: int = 1,
) -> float | None:
    """A listing's value in Ist per item, or None if no alternative can be valued.

    A seller takes whichever alternative is offered, so an ask or a sale is worth its
    cheapest; a buyer's offer is held to its best. A listing's price covers the whole
    lot: "6 Pul for 1 Vex" is one sixth of a Vex each.
    """
    values = [alternative_value(a, rates) for a in alternatives]
    known = [v for v in values if v is not None]
    if not known:
        return None
    best = max(known) if kind == "bid" else min(known)
    return best / max(lot_size, 1)


def format_value(value: float | None, rates: Mapping[str, float]) -> str:
    """A value in the largest rune it reaches: '2.4 Ber', not '30 Ist'."""
    if value is None:
        return NO_VALUE
    unit = _display_unit(value, rates)
    if unit is None:
        return f"{value:.2g} Ist"
    return format_in(value, unit, rates)


def _display_unit(value: float, rates: Mapping[str, float]) -> str | None:
    """The largest rune a value reaches, if it reaches any."""
    return next((u for u in DISPLAY_UNITS if rates.get(u) and value >= rates[u]), None)


def format_like(value: float | None, reference: float, rates: Mapping[str, float]) -> str:
    """`value` in the unit `reference` would be shown in, so the two read side by side."""
    if value is None:
        return NO_VALUE
    unit = _display_unit(reference, rates)
    return format_in(value, unit, rates) if unit else f"{value:.2g} Ist"


def format_in(value: float, currency: str, rates: Mapping[str, float]) -> str:
    """A value in a stated currency, which must have a rate."""
    return f"{value / rates[currency]:.3g} {currency.title()}"


def format_range(
    low: float | None,
    high: float | None,
    rates: Mapping[str, float],
    unit: str | None = None,
) -> str:
    """Both ends in one unit, so they can be read against each other.

    That is `unit` if given, otherwise the largest rune the higher end reaches.
    """
    if low is None or high is None:
        return NO_VALUE
    unit = unit or _display_unit(high, rates)
    if unit is None:
        return f"{low:.2g} to {high:.2g} Ist"
    return f"{low / rates[unit]:.3g} to {format_in(high, unit, rates)}"


def derive_rates(
    quotes: Mapping[str, list[tuple[str, float]]],
    seeds: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, int]]:
    """Each rune's value from what it actually traded for, in units of the other runes.

    `quotes[rune]` holds (paid_in, quantity_per_rune). Values feed each other, so this
    relaxes for a few passes from the seeds. Ist stays fixed at 1 because it is the unit.
    A rune with too few trades keeps its seed, and a result far from its seed is rejected
    as a thin market rather than a real move.
    """
    rates = dict(seeds)
    counts: dict[str, int] = {}
    for _ in range(4):
        updated = dict(rates)
        for rune, trades in quotes.items():
            priced = [q * rates[c] for c, q in trades if c in rates and c != rune]
            counts[rune] = len(priced)
            if rune == BASE_CURRENCY or len(priced) < MIN_RATE_SAMPLES:
                continue
            value = statistics.median(priced)
            seed = seeds.get(rune, value)
            if seed / MAX_RATE_DRIFT <= value <= seed * MAX_RATE_DRIFT:
                updated[rune] = value
        rates = updated
    return rates, counts
