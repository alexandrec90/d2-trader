"""The stored ladder calendar, and keeping it true."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.models import SeasonRow
from d2_trader.seasons import Season, SeasonState, apply_update, season_state
from d2_trader.sources.blizzard import ScheduleDate
from d2_trader.sources.diablo2io import BannerDate

# A fresh install needs these before its first sync, or it would spend that run believing
# no season exists. Season 15's end and 16's start are from Blizzard's Season 16 article;
# the earlier dates are from a fan tracker. A sync or the Seasons page outranks them all.
SEED_SEASONS = (
    Season(
        14,
        datetime(2026, 5, 23, 0, tzinfo=UTC),
        datetime(2026, 8, 17, 18, tzinfo=UTC),
        end_source="seed",
    ),
    Season(
        15,
        datetime(2026, 8, 22, 0, tzinfo=UTC),
        datetime(2026, 11, 9, 19, tzinfo=UTC),
        end_source="seed",
    ),
    Season(16, datetime(2026, 11, 14, 1, tzinfo=UTC)),
)

# The banner gives no time for an end date, so a day either way is the same date.
DISAGREEMENT_TOLERANCE = timedelta(hours=36)


@dataclass(frozen=True)
class Disagreement:
    number: int
    kind: str
    blizzard: datetime
    diablo2io: datetime


def _season(row: SeasonRow) -> Season:
    return Season(
        number=row.number,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        start_source=row.start_source,
        end_source=row.end_source,
        url=row.url,
    )


async def ensure_seed_seasons(session: AsyncSession) -> None:
    for season in SEED_SEASONS:
        await session.execute(
            insert(SeasonRow)
            .values(
                number=season.number,
                starts_at=season.starts_at,
                ends_at=season.ends_at,
                start_source=season.start_source,
                end_source=season.end_source,
            )
            .on_conflict_do_nothing()
        )


async def load_seasons(session: AsyncSession) -> list[Season]:
    rows = await session.execute(select(SeasonRow).order_by(SeasonRow.number))
    return [_season(row) for row in rows.scalars()]


async def current_state(session: AsyncSession, now: datetime) -> tuple[SeasonState, list[Season]]:
    seasons = await load_seasons(session)
    return season_state(now, seasons), seasons


async def apply_date(
    session: AsyncSession, number: int, kind: str, at: datetime, source: str, url: str | None = None
) -> None:
    """Record a date for a season, creating the season if this is the first word of it."""
    row = await session.get(SeasonRow, number)
    if row is None:
        if kind != "start":
            return  # An end date alone cannot place a season on the calendar.
        session.add(SeasonRow(number=number, starts_at=at, start_source=source, url=url))
        await session.flush()
        return
    updated = apply_update(_season(row), kind, at, source, url)
    row.starts_at, row.ends_at = updated.starts_at, updated.ends_at
    row.start_source, row.end_source = updated.start_source, updated.end_source
    row.url = updated.url
    await session.flush()


def find_disagreements(
    official: list[ScheduleDate], banner: list[BannerDate]
) -> list[Disagreement]:
    """Where the two independent sources differ, which means one of them needs a look."""
    stated = {(d.number, d.kind): d.at for d in official}
    conflicts = []
    for date in banner:
        blizzard = stated.get((date.number, date.kind))
        if blizzard and abs(blizzard - date.at) > DISAGREEMENT_TOLERANCE:
            conflicts.append(Disagreement(date.number, date.kind, blizzard, date.at))
    return conflicts
