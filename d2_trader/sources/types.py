"""The shape every source reduces its data to."""

from dataclasses import dataclass, field
from datetime import datetime

from d2_trader.currency import Alternative
from d2_trader.markets import MarketKey


@dataclass(frozen=True)
class ParsedListing:
    source: str
    external_id: str
    kind: str  # "ask", "bid" or "sold"
    market: MarketKey
    alternatives: list[Alternative]
    observed_at: datetime
    quantity: int = 1
    ethereal: bool | None = None
    url: str | None = None
    attrs: dict = field(default_factory=dict)
