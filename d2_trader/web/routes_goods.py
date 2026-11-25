"""Gems, shards, keys and the like: what each is worth, its offers, and a deal checker."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from d2_trader.currency import GOODS, GOODS_BY_FAMILY, MIN_RATE_SAMPLES, RUNES, display_name
from d2_trader.deals import Deal, Estimate, UnreadableDeal, judge, parse_side
from d2_trader.goods import GoodsFit
from d2_trader.models import utcnow
from d2_trader.rune_model import RuneFit
from d2_trader.seasons import Season
from d2_trader.services.calendar import current_state
from d2_trader.services.goods_values import fit_market_goods
from d2_trader.services.rune_values import RuneAsks, fit_market, measured_markets, rank_asks
from d2_trader.trend import PathPoint
from d2_trader.web.charts import Chart, rune_chart
from d2_trader.web.pages import render
from d2_trader.web.routes_runes import (
    Db,
    hours_label,
    market_query,
    open_offers,
    page_market,
    valuation_rates,
)

router = APIRouter()


@dataclass(frozen=True)
class GoodPanel:
    name: str
    today: PathPoint
    direct: int
    few: bool  # Too few trades to value anything else by.
    chart: Chart | None
    asks: RuneAsks | None = None


@dataclass(frozen=True)
class Family:
    name: str
    panels: list[GoodPanel]
    untraded: list[str]


def families(
    fit: GoodsFit, seasons: list[Season], now: datetime, asks: Mapping[str, RuneAsks]
) -> list[Family]:
    """Every family of goods, each good with trades as a panel."""
    found = []
    for family, names in GOODS_BY_FAMILY.items():
        panels, untraded = [], []
        for name in names:
            good = name.lower()
            path = fit.paths.get(good)
            if not path:
                untraded.append(name)
                continue
            chart = rune_chart(
                path, fit.observed[good], seasons, ber=None, now=now, title=f"{name} in Ist"
            )
            direct = fit.direct[good]
            few = direct < MIN_RATE_SAMPLES
            panels.append(GoodPanel(name, path[-1], direct, few, chart, asks.get(good)))
        found.append(Family(family, panels, untraded))
    return found


def estimates(fit: RuneFit, goods: GoodsFit, rates: Mapping[str, float]) -> dict[str, Estimate]:
    """What a deal may be valued by: traded runes, measured goods, and rates set by hand.

    `rates` is the final word on a value, since it holds the pins; an estimate it moves
    has no interval left.
    """
    points = {r: path[-1] for r, path in fit.paths.items() if path}
    points |= {g: goods.paths[g][-1] for g in goods.measured()}
    found = {}
    for currency in (*RUNES, *GOODS):
        value, point = rates.get(currency), points.get(currency)
        if value is None or (point is None and currency in RUNES):
            continue
        if point is not None and point.value == value:
            found[currency] = Estimate(point.value, point.low, point.high)
        else:
            found[currency] = Estimate(value, value, value)
    return found


def check_deal(
    give: str, get: str, values: Mapping[str, Estimate]
) -> tuple[Deal | None, str | None]:
    """The verdict on what was typed, or what is wrong with it."""
    if not give.strip() and not get.strip():
        return None, None
    try:
        return judge(parse_side(give), parse_side(get), values), None
    except UnreadableDeal as error:
        return None, str(error)


@router.get("/currencies", response_class=HTMLResponse)
async def currencies_page(
    request: Request,
    session: Db,
    platform: Annotated[str, Query()] = "PC",
    mode: Annotated[str, Query()] = "softcore",
    ladder: Annotated[bool, Query()] = True,
    version: Annotated[str, Query()] = "rotw",
    give: Annotated[str, Query(max_length=200)] = "",
    get: Annotated[str, Query(max_length=200)] = "",
):
    now = utcnow()
    market = page_market(platform, mode, ladder, version)
    settings = request.app.state.settings
    _, seasons = await current_state(session, now)
    fit = await fit_market(session, market, seasons, now)
    goods = await fit_market_goods(session, market, fit, seasons, now)
    rates = await valuation_rates(session, fit, goods)
    asks = rank_asks(await open_offers(session, market, settings, now), rates)
    deal, deal_error = check_deal(give, get, estimates(fit, goods, rates))
    return await render(
        request,
        session,
        "currencies.html",
        market=market,
        markets=[(m, market_query(m)) for m in await measured_markets(session)],
        families=families(goods, seasons, now, asks),
        trades=sum(goods.direct.values()),
        every=hours_label(settings.goods_poll_hours),
        give=give,
        get=get,
        deal=deal,
        deal_error=deal_error,
        names=[display_name(c) for c in (*GOODS, *reversed(RUNES))],
    )
