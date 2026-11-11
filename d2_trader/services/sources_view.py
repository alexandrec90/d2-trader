"""What the dashboard says about each data source's health."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.config import Settings
from d2_trader.services.worker import job_specs, last_run

JOB_LABELS = {
    "seasons": "Season calendar (Blizzard, diablo2.io)",
    "catalog": "Item catalog (diablo2.io)",
    "diablo2io": "Prices: diablo2.io",
    "traderie": "Prices: Traderie",
    "rates": "Rune rates (Traderie)",
}


@dataclass(frozen=True)
class SourceHealth:
    job: str
    label: str
    status: str
    last_at: datetime | None
    records: int
    detail: str | None


async def source_health(session: AsyncSession, settings: Settings) -> list[SourceHealth]:
    health = []
    for spec in job_specs(settings):
        run = await last_run(session, spec.name)
        health.append(
            SourceHealth(
                job=spec.name,
                label=JOB_LABELS[spec.name],
                status=run.status if run else "not run yet",
                last_at=run.finished_at if run else None,
                records=run.records if run else 0,
                detail=run.detail if run else None,
            )
        )
    return health
