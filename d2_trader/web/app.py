"""The FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from d2_trader.config import Settings, get_settings
from d2_trader.db import Sessions, make_engine, make_sessions
from d2_trader.services.calendar import ensure_seed_seasons
from d2_trader.web import routes_admin, routes_watch

HERE = Path(__file__).parent


def create_app(sessions: Sessions | None = None, settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    factory = sessions or make_sessions(make_engine(resolved.database_url))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with factory() as session, session.begin():
            await ensure_seed_seasons(session)
        yield

    app = FastAPI(title="D2 Trader", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.sessions = factory
    app.state.settings = resolved
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(routes_watch.router)
    app.include_router(routes_admin.router)
    return app
