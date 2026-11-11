import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from d2_trader.markets import MarketKey
from d2_trader.models import Item, SourceRun, Watchlist, utcnow
from d2_trader.services import collector
from d2_trader.services.calendar import load_seasons
from d2_trader.services.watch import add_watch
from d2_trader.services.worker import (
    JobSpec,
    has_new_watch,
    is_due,
    job_specs,
    last_run,
    outcome_of,
    run_forever,
    run_job,
    should_run,
    tick,
    write_status,
)
from d2_trader.sources.http import RobotsDenied, SourceBlocked

NOW = datetime(2026, 11, 6, tzinfo=UTC)
HOUR = timedelta(hours=1)


@pytest.fixture(autouse=True)
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "status.json"
    monkeypatch.setattr("d2_trader.services.worker.STATUS_FILE", path)
    monkeypatch.setattr(write_status, "__defaults__", (path,))
    return path


def run_at(age: timedelta, status="ok") -> SourceRun:
    return SourceRun(job="x", started_at=NOW - age, status=status)


def test_a_job_that_never_ran_is_due():
    assert is_due(None, HOUR, NOW)


def test_a_job_waits_for_its_interval():
    assert not is_due(run_at(timedelta(minutes=59)), HOUR, NOW)
    assert is_due(run_at(timedelta(minutes=61)), HOUR, NOW)


@pytest.mark.parametrize("status", ["blocked", "denied"])
def test_a_site_that_refused_us_is_left_alone_for_longer(status):
    assert not is_due(run_at(3 * HOUR, status), HOUR, NOW)
    assert is_due(run_at(5 * HOUR, status), HOUR, NOW)


def test_an_ordinary_error_is_retried_on_schedule():
    assert is_due(run_at(2 * HOUR, "error"), HOUR, NOW)


def test_outcomes_keep_refusals_distinct_from_faults():
    assert outcome_of(SourceBlocked("x")) == "blocked"
    assert outcome_of(RobotsDenied("x")) == "denied"
    assert outcome_of(collector.SourceDisabled("x")) == "disabled"
    assert outcome_of(ValueError("x")) == "error"


def test_every_source_has_a_job(settings):
    names = {spec.name for spec in job_specs(settings)}
    assert names == {"seasons", "catalog", "diablo2io", "traderie", "rates"}


def test_jobs_that_move_valuations_recheck_alerts(settings):
    moving = {spec.name for spec in job_specs(settings) if spec.collects_prices}
    assert moving == {"diablo2io", "traderie", "rates"}


def job(result, collects=False):
    async def run(_session, _settings, _now):
        if isinstance(result, Exception):
            raise result
        return result

    return JobSpec("test-job", run, HOUR, collects)


async def test_a_success_is_recorded(sessions, settings):
    run = await run_job(sessions, job(7), settings)
    assert (run.status, run.records) == ("ok", 7)
    assert run.finished_at is not None


async def test_a_block_is_recorded_not_raised(sessions, settings):
    run = await run_job(sessions, job(SourceBlocked("refused")), settings)
    assert (run.status, run.detail) == ("blocked", "refused")


@pytest.mark.parametrize("failure", [KeyError("price"), TypeError("x"), ValueError("x")])
async def test_an_unreadable_payload_is_recorded_not_raised(sessions, settings, failure):
    assert (await run_job(sessions, job(failure), settings)).status == "error"


async def test_a_bug_in_this_code_is_not_filed_as_a_bad_day_at_a_website(sessions, settings):
    with pytest.raises(RuntimeError):
        await run_job(sessions, job(RuntimeError("boom")), settings)


async def test_the_forever_loop_seeds_ticks_and_sleeps(monkeypatch, sessions, settings):
    class Stop(Exception):
        pass

    async def stop_instead_of_sleeping(_seconds):
        raise Stop

    ticks = []

    async def fake_tick(_sessions, _settings):
        ticks.append(1)

    monkeypatch.setattr("d2_trader.services.worker.asyncio.sleep", stop_instead_of_sleeping)
    monkeypatch.setattr("d2_trader.services.worker.tick", fake_tick)
    with pytest.raises(Stop):
        await run_forever(sessions, settings)
    assert ticks == [1]
    async with sessions() as session:
        assert await load_seasons(session)


async def test_a_failed_job_leaves_a_record_behind(sessions, settings):
    await run_job(sessions, job(ValueError("broke")), settings)
    async with sessions() as session:
        assert (await last_run(session, "test-job")).status == "error"


async def test_last_run_is_the_newest(sessions, settings):
    await run_job(sessions, job(1), settings)
    await run_job(sessions, job(2), settings)
    async with sessions() as session:
        assert (await last_run(session, "test-job")).records == 2


async def test_a_failed_job_rolls_back_its_writes(sessions, settings):
    async def partial(session, _settings, _now):
        session.add(Item(name="half-done", category="x"))
        await session.flush()
        raise ValueError("late failure")

    await run_job(sessions, JobSpec("partial", partial, HOUR), settings)
    async with sessions() as session:
        found = await session.execute(select(Item).where(Item.name == "half-done"))
        assert found.first() is None


async def test_a_disabled_source_is_recorded_as_disabled_and_quiet(sessions, settings):
    specs = {spec.name: spec for spec in job_specs(settings)}
    run = await run_job(sessions, specs["traderie"], settings)
    assert run.status == "disabled"
    assert "TRADERIE_ENABLED" in run.detail


async def test_tick_skips_what_is_not_due(sessions, settings, monkeypatch):
    calls = []

    async def counting(_session, _settings, _now):
        calls.append(1)
        return 0

    monkeypatch.setattr(
        "d2_trader.services.worker.job_specs", lambda _s: [JobSpec("tick-job", counting, HOUR)]
    )
    await tick(sessions, settings)
    await tick(sessions, settings)
    assert len(calls) == 1


async def add_watch_now(sessions):
    async with sessions() as session, session.begin():
        item = Item(name="Fresh", category="uniques")
        watchlist = Watchlist(name="New")
        session.add_all([item, watchlist])
        await session.flush()
        await add_watch(session, watchlist.id, item.id, MarketKey(), None)


def collecting_job(calls, name="prices"):
    async def run(_session, _settings, _now):
        calls.append(1)
        return 0

    return JobSpec(name, run, timedelta(days=1), collects_prices=True)


async def test_new_watches_are_found_by_when_they_were_added(sessions):
    before = utcnow()
    await add_watch_now(sessions)
    async with sessions() as session:
        assert await has_new_watch(session, before)
        assert not await has_new_watch(session, utcnow())


async def test_a_new_watch_does_not_wait_for_the_daily_run(sessions, settings, monkeypatch):
    # Regression: a run with nothing to watch counted, so a new item waited up to a day.
    calls = []
    monkeypatch.setattr("d2_trader.services.worker.job_specs", lambda _s: [collecting_job(calls)])
    await tick(sessions, settings)
    await add_watch_now(sessions)
    await tick(sessions, settings)
    assert len(calls) == 2


async def test_a_watch_is_only_collected_for_once_early(sessions, settings, monkeypatch):
    calls = []
    monkeypatch.setattr("d2_trader.services.worker.job_specs", lambda _s: [collecting_job(calls)])
    await tick(sessions, settings)
    await add_watch_now(sessions)
    await tick(sessions, settings)
    await tick(sessions, settings)
    assert len(calls) == 2


async def test_a_new_watch_does_not_override_a_block(sessions, settings):
    await add_watch_now(sessions)
    spec = collecting_job([])
    blocked = SourceRun(job=spec.name, started_at=NOW, status="blocked")
    async with sessions() as session:
        assert not await should_run(session, spec, blocked, NOW)


async def test_a_source_just_switched_on_runs_at_once(sessions):
    # Regression: its last run was "disabled", so it waited out the whole schedule.
    spec = JobSpec("traderie", job(1).run, timedelta(hours=1), enabled=True)
    switched_off = SourceRun(
        job=spec.name, started_at=utcnow() - timedelta(minutes=5), status="disabled"
    )
    async with sessions() as session:
        assert await should_run(session, spec, switched_off, utcnow())


async def test_a_source_still_switched_off_keeps_its_schedule(sessions):
    spec = JobSpec("traderie", job(1).run, timedelta(hours=1), enabled=False)
    switched_off = SourceRun(
        job=spec.name, started_at=utcnow() - timedelta(minutes=5), status="disabled"
    )
    async with sessions() as session:
        assert not await should_run(session, spec, switched_off, utcnow())


def test_each_job_knows_whether_its_source_is_on(settings):
    specs = {spec.name: spec for spec in job_specs(settings)}
    assert specs["traderie"].enabled is settings.traderie_enabled
    assert specs["rates"].enabled is settings.traderie_enabled
    assert specs["diablo2io"].enabled is settings.diablo2io_enabled
    assert specs["seasons"].enabled is True


async def test_only_price_jobs_care_about_new_watches(sessions, settings):
    await add_watch_now(sessions)
    spec = JobSpec("calendar", job(1).run, timedelta(days=1))
    recent = SourceRun(job=spec.name, started_at=utcnow() - HOUR, status="ok")
    async with sessions() as session:
        assert not await should_run(session, spec, recent, utcnow())


def test_status_file_keeps_the_latest_result_per_job(tmp_path):
    path = tmp_path / "logs" / "status.json"
    finished = utcnow()
    write_status(SourceRun(job="a", status="ok", records=1, finished_at=finished), path)
    write_status(SourceRun(job="b", status="blocked", records=0, detail="no"), path)
    write_status(SourceRun(job="a", status="error", records=0, detail="bad"), path)
    status = json.loads(path.read_text(encoding="utf8"))
    assert status["a"]["status"] == "error"
    assert status["b"]["detail"] == "no"
