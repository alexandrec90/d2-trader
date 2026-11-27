"""Trade up: open offers that climb from lower runes to a higher one, and what you hold."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.markets import MarketKey
from d2_trader.models import utcnow
from d2_trader.services.calendar import current_state
from d2_trader.services.goods_values import fit_market_goods
from d2_trader.services.holdings import (
    BadHolding,
    holding_groups,
    load_holdings,
    read_quantities,
    save_holdings,
)
from d2_trader.services.rune_values import FAIR_MARGIN, fit_market, measured_markets, trade_ups
from d2_trader.services.visits import visited_urls
from d2_trader.web.pages import get_session, render
from d2_trader.web.routes_runes import (
    PageMarket,
    hours_label,
    market_query,
    open_offers,
    page_market,
    valuation_rates,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]


def form_market(fields: dict[str, str]) -> MarketKey:
    """The market a holdings form was for, read as `page_market` reads a query."""
    return page_market(
        fields.get("platform", "PC"),
        fields.get("mode", "softcore"),
        fields.get("ladder", "true").lower() in ("true", "1", "yes", "on"),
        fields.get("version", "rotw"),
    )


def back_to(market: MarketKey, *, message: str | None = None, error: str | None = None):
    url = f"/trade-up?{market_query(market)}"
    if error:
        url += f"&error={quote(error)}"
    elif message:
        url += f"&message={quote(message)}"
    return RedirectResponse(url, status_code=303)


@router.get("/trade-up", response_class=HTMLResponse)
async def trade_up_page(
    request: Request,
    session: Db,
    market: PageMarket,
    show_all: Annotated[bool, Query(alias="all")] = False,
):
    """`?all=1` lists every trade up, not only those the saved holdings can pay for."""
    now = utcnow()
    _, seasons = await current_state(session, now)
    fit = await fit_market(session, market, seasons, now)
    goods = await fit_market_goods(session, market, fit, seasons, now)
    settings = request.app.state.settings
    rows = await open_offers(session, market, settings, now)
    rates = await valuation_rates(session, fit, goods)
    held = await load_holdings(session, market)
    filtered = bool(held) and not show_all
    ups = trade_ups(rows, rates, held if filtered else None)
    return await render(
        request,
        session,
        "trade_up.html",
        market=market,
        query=market_query(market),
        markets=[(m, market_query(m)) for m in await measured_markets(session)],
        groups=holding_groups(held),
        held=held,
        show_all=show_all,
        ups=ups,
        fair_margin=FAIR_MARGIN,
        every=hours_label(settings.rates_poll_hours),
        visited=await visited_urls(session, (t.listing.url for t in ups)),
    )


@router.post("/trade-up/holdings")
async def save_holdings_form(request: Request, session: Db):
    """Replace one market's saved holdings with the form's counts."""
    form = await request.form()
    fields = [(k, v) for k, v in form.multi_items() if isinstance(v, str)]
    market = form_market(dict(fields))
    try:
        held = read_quantities(fields)
    except BadHolding as problem:
        return back_to(market, error=f"Nothing saved: {problem}")
    await save_holdings(session, market, held)
    await session.commit()
    return back_to(market, message="Holdings saved")
