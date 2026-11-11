from d2_trader.markets import UNKNOWN_VERSION, MarketKey
from d2_trader.models import Item
from d2_trader.services.catalog import (
    comparable_market_ids,
    get_item_by_name,
    get_or_create_market,
    market_key,
    search_items,
    upsert_catalog,
)
from d2_trader.sources.diablo2io import CatalogEntry


async def test_market_is_created_once(session):
    first = await get_or_create_market(session, MarketKey())
    second = await get_or_create_market(session, MarketKey())
    assert first.id == second.id


async def test_market_key_round_trips(session):
    key = MarketKey("xbox", "hardcore", False, "lod")
    assert market_key(await get_or_create_market(session, key)) == key


async def test_sales_of_unknown_version_count_towards_a_market(session):
    known = await get_or_create_market(session, MarketKey())
    unstated = await get_or_create_market(session, MarketKey(game_version=UNKNOWN_VERSION))
    assert set(await comparable_market_ids(session, MarketKey())) == {known.id, unstated.id}


async def test_ladder_and_mode_are_never_blended(session):
    await get_or_create_market(session, MarketKey(ladder=False))
    await get_or_create_market(session, MarketKey(mode="hardcore"))
    mine = await get_or_create_market(session, MarketKey())
    assert await comparable_market_ids(session, MarketKey()) == [mine.id]


async def test_upsert_adds_and_refreshes(session):
    await upsert_catalog(session, [CatalogEntry("Ber", "/misc/ber-t1.html", "misc")])
    await upsert_catalog(session, [CatalogEntry("Ber", "/misc/ber-t2.html", "misc")])
    session.expire_all()
    assert (await get_item_by_name(session, "Ber")).diablo2io_path == "/misc/ber-t2.html"


async def test_upsert_leaves_a_resolved_traderie_id_alone(session):
    session.add(Item(name="Ber", category="misc", traderie_id="9"))
    await session.flush()
    await upsert_catalog(session, [CatalogEntry("Ber", "/misc/ber-t1.html", "misc")])
    session.expire_all()
    assert (await get_item_by_name(session, "Ber")).traderie_id == "9"


async def test_same_name_twice_in_one_batch_is_one_item(session):
    entries = [CatalogEntry("Ber", "/a", "misc"), CatalogEntry("Ber", "/b", "misc")]
    assert await upsert_catalog(session, entries) == 1


async def test_search_prefers_a_name_starting_with_the_text(session):
    session.add_all([Item(name="Amber Ring", category="misc"), Item(name="Ber", category="misc")])
    await session.flush()
    assert [i.name for i in await search_items(session, "ber")] == ["Ber", "Amber Ring"]


async def test_search_with_nothing_or_only_wildcards_finds_nothing(session):
    session.add(Item(name="Ber", category="misc"))
    await session.flush()
    assert await search_items(session, "  ") == []
    assert await search_items(session, "%_") == []


async def test_lookup_ignores_case_and_edges(session):
    session.add(Item(name="Harlequin Crest", category="uniques"))
    await session.flush()
    assert (await get_item_by_name(session, "  harlequin crest ")).name == "Harlequin Crest"
    assert await get_item_by_name(session, "harlequin") is None


async def test_lookup_treats_a_wildcard_literally(session):
    session.add(Item(name="Ber", category="misc"))
    await session.flush()
    assert await get_item_by_name(session, "B%") is None
