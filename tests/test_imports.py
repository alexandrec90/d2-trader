from conftest import NOW
from sqlalchemy import func, select

from d2_trader.models import Item, Listing
from d2_trader.services.imports import (
    ImportResult,
    PasteRequest,
    import_paste,
    import_rune_prices,
    pasted_listing,
    paste_scope,
    rune_item,
)
from d2_trader.sources.paste import PastedPrice


async def run_import(session, item, market, text) -> ImportResult:
    return await import_paste(session, PasteRequest(item.id, market, "sold", None, text), NOW)


async def test_lines_become_listings_and_noise_is_returned(session, item, market):
    result = await run_import(session, item, market, "2 ber\nbump\n150 fg")
    assert result.stored == 2
    assert result.skipped == ["bump"]
    rows = (await session.execute(select(Listing))).scalars().all()
    assert {r.source for r in rows} == {"d2jsp"}
    assert rows[0].attrs["line"] == "2 ber"


async def test_pasting_the_same_thread_twice_counts_once(session, item, market):
    await run_import(session, item, market, "2 ber")
    await run_import(session, item, market, "2 ber")
    count = await session.execute(select(func.count()).select_from(Listing))
    assert count.scalar_one() == 1


async def test_the_same_line_for_another_item_is_a_separate_price(
    session, item, market, make_listing
):
    other = Item(name="Other", category="uniques")
    session.add(other)
    await session.flush()
    await run_import(session, item, market, "2 ber")
    await run_import(session, other, market, "2 ber")
    count = await session.execute(select(func.count()).select_from(Listing))
    assert count.scalar_one() == 2


async def test_a_rune_list_files_each_line_under_its_rune(session, market):
    ber = Item(name="Ber", category="misc")
    jah = Item(name="Jah Rune", category="runes")
    session.add_all([ber, jah])
    await session.flush()
    result = await import_rune_prices(session, market, "ask", "Ber 150fg\nJah 2k fg\nOhm 30fg", NOW)
    assert result.stored == 2
    assert result.skipped == ["Ohm 30fg (no rune named ohm in the catalog)"]
    rows = {r.item_id: r for r in (await session.execute(select(Listing))).scalars()}
    assert rows[ber.id].alternatives == [[["fg", 150.0]]]
    assert rows[jah.id].alternatives == [[["fg", 2000.0]]]


async def test_a_rune_is_found_under_either_name(session):
    session.add(Item(name="Ber Rune", category="runes"))
    await session.flush()
    assert (await rune_item(session, "ber")).name == "Ber Rune"


def test_a_pasted_listing_carries_its_line_and_lot(market):
    request = PasteRequest(1, market, "ask", None, "2 ber 300fg")
    price = PastedPrice("2 ber 300fg", [[("fg", 300.0)]])
    listing = pasted_listing(price, paste_scope(request, NOW), request, NOW, quantity=2)
    assert (listing.quantity, listing.attrs["line"], listing.source) == (2, "2 ber 300fg", "d2jsp")


async def test_nothing_priced_stores_nothing(session, item, market):
    assert (await run_import(session, item, market, "bump")).stored == 0
