import os
import subprocess
import sys

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

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


def test_an_upgrade_logs_each_migration_it_runs():
    """The stack's `migrate` log was empty on success. `--sql` connects to nothing, so
    this needs no database."""
    env = {**os.environ, "DATABASE_URL": "postgresql+psycopg://127.0.0.1:1/none"}
    argv = [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"]
    done = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        argv, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert "Running upgrade" in done.stderr


def test_an_upgrade_waits_for_one_already_running(database_url):
    """The dashboard and the worker migrate at the same moment after a merge."""
    argv = [sys.executable, "-m", "alembic", "upgrade", "head"]
    env = {**os.environ, "DATABASE_URL": database_url, "PGOPTIONS": "-c lock_timeout=2000"}
    engine = create_engine(database_url, poolclass=NullPool)

    def upgrade():
        return subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
            argv, env=env, capture_output=True, text=True, timeout=120
        )

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(8010)"))
        held = upgrade()
    assert held.returncode != 0
    assert "lock timeout" in held.stderr
    free = upgrade()
    assert free.returncode == 0, free.stderr


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


async def test_the_cleanup_drops_asks_that_are_also_stored_as_bids(session):
    """Listing 1002606574735 wanted a Ber and was stored as a Ber ask as well."""
    cleanup = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("3d7e9b2a6c41")
    item = Item(name="Ber", category="misc")
    session.add(item)
    await session.flush()
    now = utcnow()
    price = [[("ohm", 1.0), ("vex", 1.0), ("gul", 1.0)]]
    rows = [
        ParsedListing("traderie", "twin", "ask", MarketKey(), price, now),
        ParsedListing("traderie", "twin", "bid", MarketKey(), price, now),
        ParsedListing("traderie", "twin", "sold", MarketKey(), price, now),
        ParsedListing("traderie", "ask-only", "ask", MarketKey(), price, now),
        ParsedListing("manual", "twin", "ask", MarketKey(), price, now),
    ]
    await store_listings(session, item.id, rows, now)
    await session.execute(text(cleanup.module.CLEANUP))
    kept = (await session.execute(select(Listing.source, Listing.external_id, Listing.kind))).all()
    assert sorted(kept) == [
        ("manual", "twin", "ask"),
        ("traderie", "ask-only", "ask"),
        ("traderie", "twin", "bid"),
        ("traderie", "twin", "sold"),
    ]
