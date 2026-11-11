"""Everything around the watchlists: inbox, calendar, rates, imports, source health."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import SEED_RATES, normalize_currency
from d2_trader.markets import MarketKey, d2jsp_forum_url
from d2_trader.models import utcnow
from d2_trader.seasons import typical_season_days
from d2_trader.services.calendar import apply_date, current_state
from d2_trader.services.catalog import get_item_by_name
from d2_trader.forum_gold import WINDOW_DAYS, FgEstimate, RuneReading, readings
from d2_trader.services.fg_rates import fg_markets, fg_quotes, valuation_for
from d2_trader.services.imports import PasteRequest, import_paste, import_rune_prices
from d2_trader.services.live import data_version
from d2_trader.services.notify import mark_all_read, recent_notifications, unread_count
from d2_trader.services.rates import clear_manual_rate, load_rates, rate_rows, set_manual_rate
from d2_trader.services.sources_view import source_health
from d2_trader.services.watch import evaluate_all
from d2_trader.web.charts import Chart, rate_chart
from d2_trader.web.forms import ImportForm
from d2_trader.web.pages import get_session, render
from d2_trader.web.routes_watch import redirect

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]


@router.get("/notifications", response_class=HTMLResponse)
async def notifications(request: Request, session: Db):
    return await render(
        request, session, "notifications.html", items=await recent_notifications(session)
    )


@router.post("/notifications/read")
async def read_notifications(session: Db):
    await mark_all_read(session)
    await session.commit()
    return redirect("/notifications")


@router.get("/api/live")
async def live(session: Db) -> dict:
    """What an open page polls: whether to redraw, and what to tell the operator."""
    latest = await recent_notifications(session, 1)
    newest = latest[0] if latest and latest[0].read_at is None else None
    return {
        "version": await data_version(session),
        "count": await unread_count(session),
        "latest": {"id": newest.id, "title": newest.title, "body": newest.body} if newest else None,
    }


@router.get("/seasons", response_class=HTMLResponse)
async def seasons_page(request: Request, session: Db):
    now = utcnow()
    state, seasons = await current_state(session, now)
    return await render(
        request,
        session,
        "seasons.html",
        state=state,
        seasons=sorted(seasons, key=lambda s: -s.number),
        typical_days=typical_season_days(seasons),
    )


def parse_moment(value: str) -> datetime | None:
    """A `datetime-local` value, read as UTC, as the form says."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


@router.post("/seasons")
async def save_season(
    session: Db,
    number: Annotated[int, Form(gt=0)],
    starts_at: Annotated[str, Form()] = "",
    ends_at: Annotated[str, Form()] = "",
):
    """A manual date outranks every source, so a bad parse can always be overruled."""
    start, end = parse_moment(starts_at), parse_moment(ends_at)
    if start:
        await apply_date(session, number, "start", start, "manual")
    if end:
        await apply_date(session, number, "end", end, "manual")
    await session.commit()
    return redirect("/seasons", message=f"Season {number} updated")


@dataclass(frozen=True)
class FgMarket:
    label: str
    current: FgEstimate | None
    chart: Chart | None
    ber_ist: float  # This market's own Ber value, to say what a Ber costs in fg.
    readings: list[RuneReading] = field(default_factory=list)  # The last week, by rune.


async def fg_sections(session: AsyncSession, now: datetime) -> list[FgMarket]:
    """The measured forum gold rate of every market that has rune prices in fg."""
    _, seasons = await current_state(session, now)
    sections = []
    for market_id, market in await fg_markets(session, now):
        valuation = await valuation_for(session, market_id, market, seasons, now)
        chart = rate_chart(valuation.fg_history, seasons, now=now, title=f"fg in {market.label}")
        ber = valuation.rates["ber"]
        recent = await fg_quotes(session, market, now - timedelta(days=WINDOW_DAYS))
        by_rune = readings(recent, valuation.rates)
        sections.append(FgMarket(market.label, valuation.current_fg, chart, ber, by_rune))
    return sections


@router.get("/rates", response_class=HTMLResponse)
async def rates_page(request: Request, session: Db):
    rows = await rate_rows(session)
    manual = {r.currency for r in rows if r.source == "manual"}
    return await render(
        request,
        session,
        "rates.html",
        rates=await load_rates(session, None),
        seeds=SEED_RATES,
        manual=manual,
        derived=[r for r in rows if r.source == "derived"],
        fg_markets=await fg_sections(session, utcnow()),
        fg_pinned="fg" in manual,
    )


@router.post("/rates")
async def save_rate(
    session: Db,
    currency: Annotated[str, Form()],
    value_ist: Annotated[float, Form(gt=0)],
):
    key = normalize_currency(currency)
    if key == "ist":
        return redirect("/rates", error="Ist is the unit, so it is always 1")
    await set_manual_rate(session, key, value_ist)
    await session.commit()
    return redirect("/rates", message=f"{key.title()} set to {value_ist:g} Ist")


@router.post("/rates/{currency}/clear")
async def clear_rate(currency: str, session: Db):
    await clear_manual_rate(session, currency)
    await session.commit()
    return redirect("/rates")


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request, session: Db):
    links = [
        (key.label, d2jsp_forum_url(key))
        for key in (
            MarketKey(mode=mode, ladder=ladder)
            for mode in ("softcore", "hardcore")
            for ladder in (True, False)
        )
    ]
    return await render(request, session, "import.html", forum_links=links)


@router.post("/import")
async def import_prices(request: Request, session: Db, form: Annotated[ImportForm, Form()]):
    now = utcnow()
    if form.paste_as == "runes":
        result = await import_rune_prices(session, form.market, form.kind, form.text, now)
        summary = f"Stored {result.stored} rune prices"
    else:
        found = await get_item_by_name(session, form.item)
        if found is None:
            return redirect("/import", error=f"No item named '{form.item}' in the catalog")
        paste = PasteRequest(found.id, form.market, form.kind, form.ethereal_filter, form.text)
        result = await import_paste(session, paste, now)
        summary = f"Stored {result.stored} prices for {found.name}"
    await evaluate_all(session, request.app.state.settings, now)
    await session.commit()
    if result.skipped:
        summary += f"; skipped {len(result.skipped)}: " + " | ".join(result.skipped[:5])
    return redirect("/import", message=summary)


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, session: Db):
    return await render(
        request,
        session,
        "sources.html",
        health=await source_health(session, request.app.state.settings),
    )
