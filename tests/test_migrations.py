from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text

from d2_trader.markets import MarketKey
from d2_trader.models import Base, Item, Listing, utcnow
from d2_trader.services.pricing import store_listings
from d2_trader.sources.traderie import listing_url
from d2_trader.sources.types import ParsedListing


def test_migrations_build_exactly_what_the_models_describe(database_url):
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


async def test_the_link_backfill_points_traderie_rows_at_their_own_listing(session):
    """Rows stored before the collector linked each listing kept the item's page."""
    backfill = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("8f2a1c7d4b3e")
    item = Item(name="Lo", category="misc")
    session.add(item)
    await session.flush()
    now = utcnow()
    old = "https://traderie.com/diablo2resurrected/product/3632079454"
    rows = [
        ParsedListing(
            "traderie", "1002602912749", "ask", MarketKey(), [[("ist", 6.0)]], now, url=old
        ),
        ParsedListing(
            "diablo2io", "d1", "sold", MarketKey(), [[("ist", 6.0)]], now, url="https://x/lo"
        ),
        ParsedListing("manual", "m1", "ask", MarketKey(), [[("ist", 6.0)]], now),
    ]
    await store_listings(session, item.id, rows, now)
    await session.execute(text(backfill.module.BACKFILL))
    urls = dict((await session.execute(select(Listing.source, Listing.url))).all())
    assert urls == {
        "traderie": listing_url("1002602912749"),
        "diablo2io": "https://x/lo",
        "manual": None,
    }
