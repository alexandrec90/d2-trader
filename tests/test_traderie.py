"""Built from the shape of real API responses, with seller details removed.

Everything here runs against saved shapes. No test reaches Traderie, on purpose: its
terms forbid automated access, and live collection is off unless the operator enables it.
"""

import httpx

from d2_trader.markets import MarketKey
from d2_trader.sources.http import Pace, PoliteClient
from d2_trader.sources.traderie import (
    RUNE_ITEM_IDS,
    alternatives_from,
    fetch_listings,
    find_item_id,
    in_market,
    listing_params,
    parse_listings,
    rune_quotes,
)

MARKET = MarketKey("PC", "softcore", True, "rotw")
PROPS = [
    {"name": "Mode", "type": "string", "string": "softcore", "bool": False},
    {"name": "Platform", "type": "string", "string": "PC", "bool": False},
    {"name": "Game version", "type": "string", "string": "reign of the warlock", "bool": False},
    {"name": "Ladder", "type": "bool", "string": None, "bool": True},
    {"name": "Ethereal", "type": "bool", "string": None, "bool": True},
    {"name": "Rarity", "type": "string", "string": "superior", "bool": False},
]


def price(name, quantity, group=0):
    return {"name": name, "quantity": quantity, "group": group, "type": "runes"}


def raw_listing(**overrides):
    return {
        "id": "1002479569827",
        "item_id": "2712755593",
        "amount": 1,
        "updated_at": "2026-11-06T18:53:09.306Z",
        "prices": [price("Jah Rune", 4)],
        "properties": PROPS,
        **overrides,
    }


def test_params_filter_on_every_market_dimension():
    params = listing_params("1", MarketKey("PC", "hardcore", False, "lod"), "ask")
    assert params["prop_Platform"] == "PC"
    assert params["prop_Mode"] == "hardcore"
    assert params["prop_Ladder"] == "false"
    assert params["prop_Game version"] == "lord of destruction"


def test_each_kind_asks_for_its_own_listings():
    assert listing_params("1", MARKET, "sold")["completed"] == "true"
    assert listing_params("1", MARKET, "bid")["selling"] == "false"
    assert "completed" not in listing_params("1", MARKET, "ask")


def test_parse_reads_a_listing():
    [listing] = parse_listings({"listings": [raw_listing()]}, MARKET, "ask")
    assert listing.external_id == "1002479569827"
    assert listing.alternatives == [[("jah", 4.0)]]
    assert listing.ethereal is True
    assert listing.quantity == 1
    assert listing.observed_at.year == 2026
    assert listing.attrs == {"rarity": "superior"}


def test_groups_are_alternatives_and_lots_within_a_group_are_bundled():
    prices = [price("Mal Rune", 1, 0), price("Key of Terror", 2, 1), price("Vex Rune", 1, 1)]
    assert alternatives_from(prices) == [[("mal", 1.0)], [("key of terror", 2.0), ("vex", 1.0)]]


def test_make_offer_listings_carry_no_price_to_learn_from():
    assert parse_listings({"listings": [raw_listing(prices=None)]}, MARKET, "ask") == []
    assert parse_listings({"listings": [raw_listing(prices=[])]}, MARKET, "ask") == []


def test_a_listing_from_another_market_is_rejected():
    other = {"name": "Mode", "type": "string", "string": "hardcore", "bool": False}
    listing = raw_listing(properties=[other, *PROPS[1:]])
    assert parse_listings({"listings": [listing]}, MARKET, "ask") == []


def test_a_listing_can_span_platforms():
    props = {
        "Platform": "PC,playstation",
        "Mode": "softcore",
        "Ladder": True,
        "Game version": "reign of the warlock",
    }
    assert in_market(props, MARKET)


def test_a_missing_ladder_property_means_non_ladder():
    props = {"Platform": "PC", "Mode": "softcore", "Game version": "reign of the warlock"}
    assert not in_market(props, MARKET)
    assert in_market(props, MarketKey(ladder=False))


def test_zero_amount_is_read_as_one():
    [listing] = parse_listings({"listings": [raw_listing(amount=0)]}, MARKET, "sold")
    assert listing.quantity == 1


def test_empty_payloads_parse_to_nothing():
    assert parse_listings({}, MARKET, "ask") == []
    assert parse_listings({"listings": None}, MARKET, "ask") == []


def test_rune_quotes_are_per_unit_and_skip_mixed_payment():
    listings = parse_listings(
        {
            "listings": [
                raw_listing(id="a", amount=3, prices=[price("Ist Rune", 1)]),
                raw_listing(id="b", prices=[price("Ist Rune", 1), price("Vex Rune", 1, 1)]),
            ]
        },
        MARKET,
        "sold",
    )
    assert rune_quotes(listings) == [("ist", 1 / 3)]


def test_rune_ids_cover_the_runes_that_carry_trades():
    assert {"ist", "ber", "jah", "zod"} <= set(RUNE_ITEM_IDS)


def fake_api(payload):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def client(payload):
    return PoliteClient(user_agent="t", pace=Pace(0), transport=fake_api(payload))


async def test_find_item_id_needs_an_exact_name():
    payload = {"items": [{"id": "1", "name": "Harlequin Crest Replica"}]}
    async with client(payload) as api:
        assert await find_item_id(api, "Harlequin Crest") is None


async def test_find_item_id_maps_a_catalog_rune_name():
    async with client({"items": [{"id": "9", "name": "Ber Rune"}]}) as api:
        assert await find_item_id(api, "Ber") == "9"


async def test_fetch_listings_parses_the_response():
    async with client({"listings": [raw_listing()]}) as api:
        [listing] = await fetch_listings(api, "1", MARKET, "sold")
    assert listing.kind == "sold"
