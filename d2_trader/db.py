"""The engine and session factory. Nothing else in the project builds either."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

Sessions = async_sessionmaker[AsyncSession]


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessions(engine: AsyncEngine) -> Sessions:
    return async_sessionmaker(engine, expire_on_commit=False)
