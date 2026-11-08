"""The scheduler: decides which job is due, runs it, and writes down what happened.

The last start time lives in `source_runs`, so a restart never re-polls a site early.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.config import Settings
from d2_trader.db import Sessions
from d2_trader.models import SourceRun, WatchItem, utcnow
from d2_trader.services import collector
from d2_trader.services.calendar import ensure_seed_seasons
from d2_trader.services.watch import evaluate_all
from d2_trader.sources.http import RobotsDenied, SourceBlocked

log = logging.getLogger(__name__)

STATUS_FILE = Path("logs/collector-status.json")
TICK_SECONDS = 60
# A site that refused us gets far more room than the schedule would give it.
BLOCKED_BACKOFF = 4

# What a source can do to a run: refuse, drop the connection, or send something unreadable.
# The last three are how a changed payload surfaces. Anything else is a bug here, and
# should take the worker down loudly rather than be filed as a bad day at a website.
JOB_FAILURES = (
    collector.SourceDisabled,
    SourceBlocked,
    RobotsDenied,
    httpx.HTTPError,
    SQLAlchemyError,
    ValueError,
    KeyError,
    TypeError,
)

Job = Callable[[AsyncSession, Settings, datetime], Awaitable[int]]


@dataclass(frozen=True)
class JobSpec:
    name: str
    run: Job
    every: timedelta
    collects_prices: bool = False
    # Whether its source is switched on now. A run recorded as "disabled" while it was off
    # says nothing about when the site last heard from us.
    enabled: bool = True


def job_specs(settings: Settings) -> list[JobSpec]:
    d2io, traderie = settings.diablo2io_enabled, settings.traderie_enabled
    return [
        JobSpec("seasons", collector.sync_seasons, timedelta(hours=settings.season_poll_hours)),
        JobSpec(
            "catalog",
            collector.sync_catalog,
            timedelta(days=settings.catalog_poll_days),
            enabled=d2io,
        ),
        JobSpec(
            "diablo2io",
            collector.collect_diablo2io,
            timedelta(hours=settings.diablo2io_poll_hours),
            collects_prices=True,
            enabled=d2io,
        ),
        JobSpec(
            "traderie",
            collector.collect_traderie,
            timedelta(minutes=settings.traderie_poll_minutes),
            collects_prices=True,
            enabled=traderie,
        ),
        # Rates move every valuation, so a fresh rate re-checks the alerts too.
        JobSpec(
            "rates",
            collector.refresh_rates,
            timedelta(hours=settings.rates_poll_hours),
            collects_prices=True,
            enabled=traderie,
        ),
    ]


def is_due(last: SourceRun | None, every: timedelta, now: datetime) -> bool:
    if last is None:
        return True
    interval = every * BLOCKED_BACKOFF if last.status in ("blocked", "denied") else every
    return now - last.started_at >= interval


async def has_new_watch(session: AsyncSession, since: datetime) -> bool:
    query = select(WatchItem.id).where(WatchItem.created_at > since).limit(1)
    return (await session.execute(query)).first() is not None


async def should_run(
    session: AsyncSession, spec: JobSpec, last: SourceRun | None, now: datetime
) -> bool:
    """Due by the schedule, just switched on, or a price job with an unvisited item.

    A daily source would otherwise leave a new watch blank until tomorrow, and a source
    switched on would wait out a schedule it never used. A refusal still wins: being told
    to wait is not undone by wanting something new.
    """
    if is_due(last, spec.every, now):
        return True
    if last is not None and last.status == "disabled" and spec.enabled:
        return True
    if last is None or last.status != "ok" or not spec.collects_prices:
        return False
    return await has_new_watch(session, last.started_at)


async def last_run(session: AsyncSession, job: str) -> SourceRun | None:
    query = (
        select(SourceRun).where(SourceRun.job == job).order_by(SourceRun.started_at.desc()).limit(1)
    )
    return (await session.execute(query)).scalars().first()


def outcome_of(error: Exception) -> str:
    """How a failed job is reported. `blocked` and `denied` mean "leave them alone"."""
    if isinstance(error, collector.SourceDisabled):
        return "disabled"
    if isinstance(error, SourceBlocked):
        return "blocked"
    if isinstance(error, RobotsDenied):
        return "denied"
    return "error"


async def run_job(sessions: Sessions, spec: JobSpec, settings: Settings) -> SourceRun:
    """Run one job in its own transaction. Its failure is recorded, and contained."""
    started = utcnow()
    run = SourceRun(job=spec.name, started_at=started)
    try:
        async with sessions() as session, session.begin():
            run.records = await spec.run(session, settings, started)
            if spec.collects_prices:
                await evaluate_all(session, settings, utcnow())
        run.status = "ok"
    except JOB_FAILURES as error:
        run.status = outcome_of(error)
        run.detail = str(error) or type(error).__name__
        if run.status in ("blocked", "denied", "error"):
            log.warning("%s: %s: %s", spec.name, run.status, run.detail)
    run.finished_at = utcnow()
    async with sessions() as session, session.begin():
        session.add(run)
    write_status(run)
    return run


def write_status(run: SourceRun, path: Path = STATUS_FILE) -> None:
    """The latest result of each job, for an agent reading a failure from a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    status = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    status[run.job] = {
        "status": run.status,
        "records": run.records,
        "detail": run.detail,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    path.write_text(json.dumps(status, indent=2), encoding="utf8")


async def tick(sessions: Sessions, settings: Settings) -> list[SourceRun]:
    """Run whatever is due, in order. A slow site delays the next one, which is fine."""
    runs = []
    for spec in job_specs(settings):
        async with sessions() as session:
            last = await last_run(session, spec.name)
            due = await should_run(session, spec, last, utcnow())
        if due:
            runs.append(await run_job(sessions, spec, settings))
    return runs


async def run_forever(sessions: Sessions, settings: Settings) -> None:
    async with sessions() as session, session.begin():
        await ensure_seed_seasons(session)
    while True:
        await tick(sessions, settings)
        async with sessions() as session, session.begin():
            await collector.send_season_events(session, settings, utcnow())
        await asyncio.sleep(TICK_SECONDS)
