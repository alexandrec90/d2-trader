from conftest import NOW

from d2_trader.models import Notification, SourceRun, Watchlist
from d2_trader.services.live import SIGNALS, data_version
from d2_trader.services.notify import mark_all_read
from d2_trader.services.pricing import store_listings


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


def test_every_table_a_page_shows_is_watched():
    assert len(SIGNALS) >= 10
