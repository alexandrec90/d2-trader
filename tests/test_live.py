from datetime import timedelta

from conftest import NOW

from d2_trader.markets import MarketKey
from d2_trader.models import Notification, SourceRun, Watchlist
from d2_trader.services.holdings import save_holdings
from d2_trader.services.live import SIGNALS, data_version
from d2_trader.services.notify import mark_all_read
from d2_trader.services.pricing import store_listings
from d2_trader.services.visits import record_visit


async def test_nothing_changed_means_the_same_version(session):
    assert await data_version(session) == await data_version(session)


async def test_a_finished_job_changes_it(session):
    before = await data_version(session)
    session.add(SourceRun(job="seasons", status="ok"))
    await session.flush()
    assert await data_version(session) != before


async def test_a_new_price_changes_it(session, item, make_listing):
    before = await data_version(session)
    await store_listings(session, item.id, [make_listing("a", 2)], NOW)
    assert await data_version(session) != before


async def test_reading_the_inbox_changes_it(session):
    session.add(Notification(kind="price", dedupe_key="k", title="t", body="b", delivery={}))
    await session.flush()
    before = await data_version(session)
    await mark_all_read(session)
    await session.flush()
    assert await data_version(session) != before


async def test_a_new_list_changes_it(session):
    before = await data_version(session)
    session.add(Watchlist(name="x"))
    await session.flush()
    assert await data_version(session) != before


async def test_saved_holdings_change_it(session):
    before = await data_version(session)
    await save_holdings(session, MarketKey(), {"pul": 15})
    after = await data_version(session)
    assert after != before
    await save_holdings(session, MarketKey(), {})  # Clearing them is a change too.
    assert await data_version(session) != after


async def test_opening_a_listing_changes_it(session):
    """The offer tables mark opened listings, so an open page has to redraw for one."""
    before = await data_version(session)
    await record_visit(session, "https://traderie.com/listing/1", NOW)
    after = await data_version(session)
    assert after != before
    # Opening another while the first page is still loading is a change of its own.
    await record_visit(session, "https://traderie.com/listing/2", NOW)
    assert await data_version(session) != after


async def test_reopening_a_listing_changes_it(session):
    await record_visit(session, "https://traderie.com/listing/1", NOW)
    before = await data_version(session)
    await record_visit(session, "https://traderie.com/listing/1", NOW + timedelta(minutes=1))
    assert await data_version(session) != before


def test_every_table_a_page_shows_is_watched():
    assert len(SIGNALS) >= 14
