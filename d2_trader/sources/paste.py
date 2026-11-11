"""Prices a person copied by hand, which is how d2jsp data gets in.

d2jsp's rules forbid automation ("No botting or automation of any features within
d2jsp", including page refreshing) and its forums sit behind a bot challenge, so nothing
here fetches it. The operator reads a thread and pastes lines into the Import page, naming
the item and market there, so this only has to find the price in each line.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from d2_trader.currency import RUNES, Alternative, normalize_currency

SOURCE = "d2jsp"
# Words that are also rune names ("um", "lo", "sol", "eth") read as prices only with a
# quantity in front. High runes are worth naming on their own: "selling for ber".
BARE_WORDS = "ist|gul|vex|ohm|sur|ber|jah|cham|zod"

RUNE_WORDS = "|".join(RUNES)

FG_PRICE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(k?)\s*fg\b", re.IGNORECASE)
NAMED_RUNE = re.compile(
    rf"(?<![\w.])(?:(\d+)\s*x?\s*)?({RUNE_WORDS})(?:\s+runes?)?\b", re.IGNORECASE
)
COUNTED_RUNE = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*x?\s*({RUNE_WORDS})\b", re.IGNORECASE)
BARE_RUNE = re.compile(rf"(?<![\w.])({BARE_WORDS})\b", re.IGNORECASE)
OR_SPLIT = re.compile(r"\s+or\s+|\s*/\s*|\s*\|\s*", re.IGNORECASE)


@dataclass(frozen=True)
class PastedPrice:
    line: str
    alternatives: list[Alternative]

    def external_id(self, scope: str) -> str:
        """Stable for one line within `scope`: "2 ber" means nothing without its item."""
        return hashlib.sha256(f"{scope}|{self.line}".encode()).hexdigest()[:32]


def _lots_in(clause: str) -> Alternative:
    lots: Alternative = [
        ("fg", float(amount) * (1000 if thousands else 1))
        for amount, thousands in FG_PRICE.findall(clause)
    ]
    remainder = FG_PRICE.sub(" ", clause)
    counted = COUNTED_RUNE.findall(remainder)
    lots += [(normalize_currency(rune), float(amount)) for amount, rune in counted]
    remainder = COUNTED_RUNE.sub(" ", remainder)
    lots += [(normalize_currency(rune), 1.0) for rune in BARE_RUNE.findall(remainder)]
    return lots


def parse_line(line: str) -> PastedPrice | None:
    """The price in a line, or None if it states none."""
    alternatives = [lots for clause in OR_SPLIT.split(line) if (lots := _lots_in(clause))]
    return PastedPrice(line.strip(), alternatives) if alternatives else None


def parse_paste(text: str) -> tuple[list[PastedPrice], list[str]]:
    """Prices found, and the non-blank lines that had none, to show back to the operator."""
    return _split(text, parse_line)


@dataclass(frozen=True)
class RunePrice:
    """One line of a rune price list: so many of a rune, for so much forum gold."""

    rune: str
    quantity: int
    price: PastedPrice


def parse_rune_line(line: str) -> RunePrice | None:
    """'Ber 150fg', '2 jah - 360 fg', 'Ist rune: 8fg'. Exactly one rune and one fg price.

    A line naming two runes ("Lo / Sur 50fg") is skipped rather than guessed at.
    """
    fg = FG_PRICE.findall(line)
    named = NAMED_RUNE.findall(FG_PRICE.sub(" ", line))
    if len(fg) != 1 or len({rune.lower() for _, rune in named}) != 1:
        return None
    amount, thousands = fg[0]
    count, rune = named[0]
    total = float(amount) * (1000 if thousands else 1)
    price = PastedPrice(line.strip(), [[("fg", total)]])
    return RunePrice(normalize_currency(rune), int(count or 1), price)


def parse_rune_paste(text: str) -> tuple[list[RunePrice], list[str]]:
    """A rune price list, as d2jsp threads give them, and the lines it could not read."""
    return _split(text, parse_rune_line)


def _split[T](text: str, parse: Callable[[str], T | None]) -> tuple[list[T], list[str]]:
    found, skipped = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            parsed = parse(line)
            if parsed is None:
                skipped.append(line)
            else:
                found.append(parsed)
    return found, skipped
