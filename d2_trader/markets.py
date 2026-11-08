"""What "the same market" means. Prices are only comparable inside one of these."""

from dataclasses import dataclass

PLATFORMS = ("PC", "playstation", "xbox", "switch")
MODES = ("softcore", "hardcore")

# Short key -> Traderie's "Game version" property value.
GAME_VERSIONS = {
    "rotw": "reign of the warlock",
    "lod": "lord of destruction",
    "classic": "classic (base game)",
}
VERSION_LABELS = {"rotw": "RotW", "lod": "LoD", "classic": "Classic"}
# diablo2.io's sold entries do not say which version they were traded on.
UNKNOWN_VERSION = "unknown"

# d2jsp's PC trading forums, keyed (mode, ladder, version). Links for a person pasting
# prices by hand; nothing in this project fetches them.
D2JSP_FORUMS = {
    ("softcore", True, "rotw"): 271,
    ("hardcore", True, "rotw"): 123,
    ("softcore", False, "rotw"): 268,
    ("hardcore", False, "rotw"): 229,
    ("softcore", True, "lod"): 394,
    ("softcore", False, "lod"): 393,
    ("softcore", True, "classic"): 176,
    ("softcore", False, "classic"): 245,
}


@dataclass(frozen=True)
class MarketKey:
    platform: str = "PC"
    mode: str = "softcore"
    ladder: bool = True
    game_version: str = "rotw"

    @property
    def label(self) -> str:
        version = VERSION_LABELS.get(self.game_version, self.game_version)
        ladder = "Ladder" if self.ladder else "Non-Ladder"
        return f"{self.platform} · {self.mode.title()} · {ladder} · {version}"


def d2jsp_forum_url(market: MarketKey) -> str | None:
    """The d2jsp trading forum for a market, or None when it has no PC forum of its own."""
    if market.platform != "PC":
        return None
    forum = D2JSP_FORUMS.get((market.mode, market.ladder, market.game_version))
    return f"https://forums.d2jsp.org/forum.php?f={forum}" if forum else None
