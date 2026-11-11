"""What the dashboard's forms may submit. Anything else is refused before a handler runs."""

from typing import Literal

from pydantic import BaseModel, Field

from d2_trader.markets import MarketKey

Metric = Literal["estimate", "lowest_ask", "median_ask", "sold_median", "highest_bid"]
Direction = Literal["at_or_below", "at_or_above"]


def parse_ethereal(value: str) -> bool | None:
    """The form's three choices, as the filter stored on a watch."""
    return {"yes": True, "no": False}.get(value)


class MarketForm(BaseModel):
    platform: Literal["PC", "playstation", "xbox", "switch"]
    mode: Literal["softcore", "hardcore"]
    ladder: Literal["yes", "no"]
    game_version: Literal["rotw", "lod", "classic"]
    ethereal: Literal["any", "yes", "no"] = "any"

    @property
    def market(self) -> MarketKey:
        return MarketKey(self.platform, self.mode, self.ladder == "yes", self.game_version)

    @property
    def ethereal_filter(self) -> bool | None:
        return parse_ethereal(self.ethereal)


class AddItemForm(MarketForm):
    item: str


class ImportForm(MarketForm):
    # "runes" is a rune price list in fg, where each line names its own rune. Not `mode`:
    # that is the market's softcore or hardcore, and the two would overwrite each other.
    paste_as: Literal["item", "runes"] = "item"
    item: str = ""
    text: str
    kind: Literal["ask", "bid", "sold"] = "ask"


class RuleForm(BaseModel):
    metric: Metric
    direction: Direction
    threshold_amount: float = Field(gt=0)
    threshold_currency: str
    cooldown_hours: int = Field(default=12, ge=0)
