"""Traderie's listings API: asks, bids and, best of all, completed trades.

**Off unless `TRADERIE_ENABLED=true`.** Traderie's Terms of Service, section 4, forbid
automated access, so the collector refuses to run without that setting; read README,
"Data sources", before setting it. The API is undocumented, and may change without notice.
"""

from collections import defaultdict
from datetime import datetime

from d2_trader.currency import Alternative, normalize_currency
from d2_trader.markets import GAME_VERSIONS, MarketKey
from d2_trader.sources.http import PoliteClient
from d2_trader.sources.types import ParsedListing

SOURCE = "traderie"
API = "https://traderie.com/api/diablo2resurrected"
SITE = "https://traderie.com/diablo2resurrected"
# An ask must say `selling=true`: without it Traderie mixes in "they want" listings, and a
# bid for a Ber, read as an ask, is a Ber offered at the price its poster would pay.
KIND_PARAMS = {
    "ask": {"selling": "true"},
    "bid": {"selling": "false"},
    "sold": {"completed": "true"},
}
# Stable ids, listed so measuring rates does not cost a search per rune per market.
RUNE_ITEM_IDS = {
    "pul": "2899268590",
    "um": "2654038235",
    "mal": "2401638276",
    "ist": "2290642411",
    "gul": "4160776515",
    "vex": "3806138905",
    "ohm": "3896329590",
    "lo": "3632079454",
    "sur": "3382986705",
    "ber": "4149485449",
    "jah": "2552039455",
    "cham": "3191411278",
    "zod": "4067651730",
}


def listing_url(listing_id: str) -> str:
    """A listing's own page. The item's page says nothing about one listing.

    The operator confirmed this shape in a browser on 2026-11-23. A listing that has been
    taken down has no page any more, so a link to an old one can still be dead.
    """
    return f"{SITE}/listing/{listing_id}"


def listing_params(item_id: str, market: MarketKey, kind: str) -> dict[str, str]:
    """The server filters on all of these. `parse_listings` still checks each result."""
    return {
        "item": item_id,
        "prop_Platform": market.platform,
        "prop_Mode": market.mode,
        "prop_Ladder": "true" if market.ladder else "false",
        "prop_Game version": GAME_VERSIONS[market.game_version],
        **KIND_PARAMS[kind],
    }


def _properties(raw: dict) -> dict:
    found = {}
    for prop in raw.get("properties") or []:
        found[prop["name"]] = prop["bool"] if prop.get("type") == "bool" else prop.get("string")
    return found


def in_market(props: dict, market: MarketKey) -> bool:
    """Whether a listing belongs to `market`. A listing can name several platforms."""
    platforms = (props.get("Platform") or "").split(",")
    return (
        market.platform in platforms
        and props.get("Mode") == market.mode
        and bool(props.get("Ladder")) == market.ladder
        and props.get("Game version") == GAME_VERSIONS[market.game_version]
    )


def alternatives_from(prices: list[dict]) -> list[Alternative]:
    """Traderie's price groups: any one group buys it, and a whole group is paid at once."""
    groups: dict[int, Alternative] = defaultdict(list)
    for price in prices:
        groups[price["group"]].append((normalize_currency(price["name"]), float(price["quantity"])))
    return [groups[g] for g in sorted(groups)]


def lot_size(raw: dict) -> int:
    """How many items a listing's price buys.

    `amount` is how many the seller has. The price covers them all ("3 Mal for 2 Ist")
    unless `stock` is set, when it buys one of them. Some listings report an amount of 0.
    """
    if raw.get("stock"):
        return 1
    return max(int(raw.get("amount") or 1), 1)


def poster(raw: dict) -> str | None:
    """Who posted a listing, by their Traderie username.

    The operator saw `"username": "Larzuk001"` in a listings response on 2026-11-26 but
    not where it sits, so both a top-level field and a `user` block are read.
    """
    user = raw.get("user")
    name = raw.get("username") or (user.get("username") if isinstance(user, dict) else None)
    return str(name) if name else None


def parse_listings(payload: dict, market: MarketKey, kind: str) -> list[ParsedListing]:
    """Listings with a stated price. "Make offer" ones carry no price to learn from."""
    parsed = []
    for raw in payload.get("listings") or []:
        props = _properties(raw)
        prices = raw.get("prices") or []
        if not prices or not in_market(props, market):
            continue
        parsed.append(
            ParsedListing(
                source=SOURCE,
                external_id=str(raw["id"]),
                kind=kind,
                market=market,
                alternatives=alternatives_from(prices),
                observed_at=datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00")),
                quantity=lot_size(raw),
                ethereal=bool(props.get("Ethereal")),
                url=listing_url(str(raw["id"])),
                attrs={"rarity": props.get("Rarity"), "poster": poster(raw)},
            )
        )
    return parsed


async def find_item_id(client: PoliteClient, name: str) -> str | None:
    """Traderie's id for an item, on an exact name match only.

    diablo2.io's catalog calls a rune "Ber" where Traderie says "Ber Rune".
    """
    response = await client.get(f"{API}/items", params={"search": name, "tags": "true"})
    wanted = {name.lower(), f"{name.lower()} rune"}
    for item in response.json().get("items") or []:
        if item["name"].lower() in wanted:
            return str(item["id"])
    return None


async def fetch_listings(
    client: PoliteClient, item_id: str, market: MarketKey, kind: str
) -> list[ParsedListing]:
    response = await client.get(f"{API}/listings", params=listing_params(item_id, market, kind))
    return parse_listings(response.json(), market, kind)
