"""Shared fixtures. Database tests run against real Postgres, each inside a rollback.

`listings` relies on Postgres-only features (ON CONFLICT, NULLS NOT DISTINCT), so a
stand-in database would test something else. Migrations are applied once per run, so the
suite tests the schema Alembic builds, not the one the models imply.
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from d2_trader.config import Settings, get_settings
from d2_trader.db import make_engine
from d2_trader.markets import MarketKey
from d2_trader.models import Item
from d2_trader.seasons import Season, season_state
from d2_trader.sources.http import LAST_REQUEST
from d2_trader.sources.types import ParsedListing
from d2_trader.web.app import create_app

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
S15 = Season(15, datetime(2026, 8, 22, tzinfo=UTC), datetime(2026, 11, 9, 19, tzinfo=UTC))
S16 = Season(16, datetime(2026, 11, 14, 1, tzinfo=UTC))


@pytest.fixture(autouse=True)
def fresh_pacing():
    """Pacing is remembered per site across clients; one test's sites are not another's."""
    LAST_REQUEST.clear()
    yield
    LAST_REQUEST.clear()


def pytest_asyncio_loop_factories(config, item):
    """psycopg's async mode cannot run on Windows' default proactor loop."""
    return {"selector": asyncio.SelectorEventLoop} if sys.platform == "win32" else None


def isolated_database_url(url: str) -> str:
    """`<db>_test` on the same server, created if needed.

    Never the database itself: a running worker writes there, and a rollback stops a test
    changing real data but not reading it, so tests would see prices that are not theirs.
    """
    parsed = make_url(url)
    name = f"{parsed.database}_test"
    engine = create_engine(parsed, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        exists = connection.execute(
            text("select 1 from pg_database where datname = :name"), {"name": name}
        ).first()
        if not exists:
            connection.execute(text(f'create database "{name}"'))
    engine.dispose()
    return parsed.set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = isolated_database_url(get_settings().database_url)
    os.environ["DATABASE_URL"] = url  # Where `migrations/env.py` reads it from.
    command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest_asyncio.fixture
async def sessions(database_url):
    """A session factory whose every commit stays inside one rolled-back transaction."""
    engine = make_engine(database_url)
    async with engine.connect() as connection:
        outer = await connection.begin()
        yield async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        await outer.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def session(sessions):
    async with sessions() as db_session:
        yield db_session


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        traderie_enabled=False,
        discord_webhook_url=None,
        ntfy_url=None,
        diablo2io_min_interval_s=0,
        traderie_min_interval_s=0,
        blizzard_min_interval_s=0,
    )


@pytest_asyncio.fixture
async def item(session) -> Item:
    row = Item(name="Test Crest", category="uniques", diablo2io_path="/uniques/test-crest-t1.html")
    session.add(row)
    await session.flush()
    return row


@pytest_asyncio.fixture
async def client(sessions, settings):
    """The dashboard, over ASGI, on the same rolled-back transaction as the test."""
    app = create_app(sessions, settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
def market() -> MarketKey:
    return MarketKey()


@pytest.fixture
def mid_season():
    """The season state at `NOW`: day 40 of Season 15."""
    return season_state(NOW, [S15, S16])


@pytest.fixture
def make_listing(market):
    """A `ParsedListing` with only what a test cares about stated."""

    def build(external_id: str, price: float, currency: str = "ist", **fields) -> ParsedListing:
        defaults = {
            "source": "traderie",
            "kind": "sold",
            "market": market,
            "observed_at": NOW,
            "alternatives": [[(currency, price)]],
        }
        return ParsedListing(external_id=external_id, **{**defaults, **fields})

    return build
