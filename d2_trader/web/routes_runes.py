"""Rune values over time: one small chart per rune, for one market at a time."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import RUNES
from d2_trader.markets import GAME_VERSIONS, MODES, PLATFORMS, MarketKey
from d2_trader.models import utcnow
from d2_trader.rune_model import RuneFit
from d2_trader.seasons import Season
from d2_trader.services.calendar import current_state
from d2_trader.services.rates import load_rates, manual_currencies
from d2_trader.services.rune_values import (
    RuneAsks,
    active_asks,
    fit_market,
    measured_markets,
    rank_asks,
)
from d2_trader.sources.traderie import RUNE_ITEM_IDS
from d2_trader.trend import PathPoint
from d2_trader.web.charts import Chart, rune_chart
from d2_trader.web.pages import get_session, render

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]
# The runes Traderie is asked about, most valuable first: the rest only ever appear as
# payment, too rarely to chart.
CHARTED = sorted(RUNE_ITEM_IDS, key=RUNES.index, reverse=True)


@dataclass(frozen=True)
class RunePanel:
    rune: str
    today: PathPoint
    ber: float  # Today's Ber in Ist, to say the value the way traders do.
    direct: int
    chart: Chart | None
    asks: RuneAsks | None = None


def hours_label(hours: int) -> str:
    """A poll interval as the page says it: "every hour", "every 12 hours"."""
    return "hour" if hours == 1 else f"{hours} hours"


def market_query(market: MarketKey) -> str:
    ladder = "true" if market.ladder else "false"
    return (
        f"platform={market.platform}&mode={market.mode}&ladder={ladder}"
        f"&version={market.game_version}"
    )


def panels(
    fit: RuneFit,
    seasons: list[Season],
    now: datetime,
    asks: Mapping[str, RuneAsks] | None = None,
) -> list[RunePanel]:
    """A chart and the cheapest asks for every charted rune, in order of value."""
    asks = asks or {}
    ber = fit.today().get("ber")
    if not ber:
        return []
    found = []
    for rune in CHARTED:
        path = fit.paths[rune]
        chart = rune_chart(
            path,
            fit.observed.get(rune, []),
            seasons,
            ber=ber,
            now=now,
            title=f"{rune.title()} in Ist",
        )
        direct = fit.direct.get(rune, 0)
        found.append(RunePanel(rune, path[-1], ber, direct, chart, asks.get(rune)))
    return found


async def valuation_rates(session: AsyncSession, fit: RuneFit) -> dict[str, float]:
    """Today's modelled rune values, over the seeds; a rate pinned by hand still wins."""
    base = await load_rates(session, None)
    pinned = {c: base[c] for c in await manual_currencies(session) if c in base}
    return {**base, **fit.today(), **pinned}


@router.get("/runes", response_class=HTMLResponse)
async def runes_page(
    request: Request,
    session: Db,
    platform: Annotated[str, Query()] = "PC",
    mode: Annotated[str, Query()] = "softcore",
    ladder: Annotated[bool, Query()] = True,
    version: Annotated[str, Query()] = "rotw",
):
    now = utcnow()
    market = MarketKey(
        platform if platform in PLATFORMS else "PC",
        mode if mode in MODES else "softcore",
        ladder,
        version if version in GAME_VERSIONS else "rotw",
    )
    _, seasons = await current_state(session, now)
    fit = await fit_market(session, market, seasons, now)
    markets = await measured_markets(session)
    # Asks are fetched by the rates job: one seen in its latest run is still up.
    every = request.app.state.settings.rates_poll_hours
    seen_since = now - timedelta(hours=every + 1)
    rows = await active_asks(session, market, seen_since)
    asks = rank_asks(rows, await valuation_rates(session, fit))
    return await render(
        request,
        session,
        "runes.html",
        market=market,
        markets=[(m, market_query(m)) for m in markets],
        panels=panels(fit, seasons, now, asks),
        trades=fit.trades,
        every=hours_label(every),
    )
