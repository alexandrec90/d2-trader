from d2_trader.models import SourceRun, utcnow
from d2_trader.services.sources_view import JOB_LABELS, SourceHealth, source_health
from d2_trader.services.worker import job_specs


async def test_every_job_has_a_row_before_anything_has_run(session, settings):
    health = await source_health(session, settings)
    assert {h.job for h in health} == set(JOB_LABELS)
    assert {h.status for h in health} == {"not run yet"}
    assert all(isinstance(h, SourceHealth) for h in health)


async def test_the_latest_run_shows(session, settings):
    session.add(SourceRun(job="traderie", status="disabled", detail="off", finished_at=utcnow()))
    await session.flush()
    health = {h.job: h for h in await source_health(session, settings)}
    assert health["traderie"].status == "disabled"
    assert health["traderie"].detail == "off"


def test_every_job_is_labelled(settings):
    assert {spec.name for spec in job_specs(settings)} <= set(JOB_LABELS)
