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
SITE = "https://traderie.com/diablo2resurrected/product"
KIND_PARAMS = {"ask": {}, "bid": {"selling": "false"}, "sold": {"completed": "true"}}
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
                quantity=max(int(raw.get("amount") or 1), 1),
                ethereal=bool(props.get("Ethereal")),
                url=f"{SITE}/{raw['item_id']}",
                attrs={"rarity": props.get("Rarity")},
            )
        )
    return parsed


def rune_quotes(listings: list[ParsedListing]) -> list[tuple[str, float]]:
    """What a rune fetched per unit, from sales paid in exactly one kind of thing."""
    quotes = []
    for listing in listings:
        if len(listing.alternatives) == 1 and len(listing.alternatives[0]) == 1:
            currency, quantity = listing.alternatives[0][0]
            quotes.append((currency, quantity / listing.quantity))
    return quotes


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
