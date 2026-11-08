"""The dashboard, watchlists, the item page and alert rules."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.currency import normalize_currency
from d2_trader.markets import d2jsp_forum_url
from d2_trader.models import AlertRule, Watchlist, WatchItem, utcnow
from d2_trader.services.calendar import current_state
from d2_trader.services.catalog import get_item_by_name, market_key, search_items
from d2_trader.services.pricing import MarketView, estimate_path, history, recent_listings
from d2_trader.services.rates import load_rates
from d2_trader.services.sources_view import source_health
from d2_trader.services.watch import (
    add_watch,
    describe_rule,
    get_watch,
    list_watchlists,
    query_for,
    threshold_in_ist,
    view_for,
)
from d2_trader.stats import daily_history
from d2_trader.web.charts import price_chart
from d2_trader.web.forms import AddItemForm, RuleForm
from d2_trader.web.pages import get_session, render

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]
HISTORY_DAYS = 120
RECENT_DAYS = 14


@dataclass(frozen=True)
class WatchRow:
    watch: WatchItem
    market_label: str
    view: MarketView
    rates: dict[str, float]


def redirect(url: str, *, error: str | None = None, message: str | None = None):
    if error:
        url = f"{url}?error={quote(error)}"
    elif message:
        url = f"{url}?message={quote(message)}"
    return RedirectResponse(url, status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Db):
    now = utcnow()
    settings = request.app.state.settings
    state, _ = await current_state(session, now)
    lists = []
    for watchlist in await list_watchlists(session):
        rows = []
        for watch in sorted(watchlist.entries, key=lambda w: w.item.name):
            view, valuation = await view_for(session, watch, state, now, settings)
            rows.append(WatchRow(watch, market_key(watch.market).label, view, valuation.rates))
        lists.append((watchlist, rows))
    return await render(
        request,
        session,
        "dashboard.html",
        state=state,
        lists=lists,
        health=await source_health(session, settings),
    )


@router.get("/api/items")
async def items_api(q: str, session: Db) -> list[dict]:
    return [
        {"name": item.name, "category": item.category} for item in await search_items(session, q)
    ]


@router.post("/watchlists")
async def create_watchlist(session: Db, name: Annotated[str, Form()]):
    name = name.strip()
    if not name:
        return redirect("/", error="A watchlist needs a name")
    session.add(Watchlist(name=name))
    await session.commit()
    return redirect("/")


@router.post("/watchlists/{watchlist_id}/delete")
async def delete_watchlist(watchlist_id: int, session: Db):
    watchlist = await session.get(Watchlist, watchlist_id)
    if watchlist:
        await session.delete(watchlist)
        await session.commit()
    return redirect("/")


@router.post("/watchlists/{watchlist_id}/items")
async def add_item(watchlist_id: int, session: Db, form: Annotated[AddItemForm, Form()]):
    found = await get_item_by_name(session, form.item)
    if found is None:
        return redirect("/", error=f"No item named '{form.item}' in the catalog")
    await add_watch(session, watchlist_id, found.id, form.market, form.ethereal_filter)
    await session.commit()
    return redirect("/")


@router.post("/watch/{watch_id}/delete")
async def delete_watch(watch_id: int, session: Db):
    watch = await get_watch(session, watch_id)
    if watch:
        await session.delete(watch)
        await session.commit()
    return redirect("/")


@router.get("/watch/{watch_id}", response_class=HTMLResponse)
async def watch_detail(watch_id: int, request: Request, session: Db):
    watch = await get_watch(session, watch_id)
    if watch is None:
        raise HTTPException(404, "No such watched item")
    now = utcnow()
    settings = request.app.state.settings
    state, seasons = await current_state(session, now)
    view, valuation = await view_for(session, watch, state, now, settings)
    rates = valuation.rates
    wanted = query_for(watch)
    since = now - timedelta(days=HISTORY_DAYS)
    quotes = await history(session, wanted, valuation, since)
    days = daily_history(quotes)
    path = estimate_path(quotes, seasons, wanted.market.ladder, now.date())
    rules = [(r, describe_rule(r), threshold_in_ist(r, rates)) for r in watch.rules]
    return await render(
        request,
        session,
        "watch.html",
        watch=watch,
        market=wanted.market,
        state=state,
        view=view,
        rates=rates,
        days=days[::-1],
        chart=price_chart(
            days, seasons, rates, now=now, title=f"{watch.item.name} price history", path=path
        ),
        rules=rules,
        listings=await recent_listings(session, wanted, now - timedelta(days=RECENT_DAYS)),
        d2jsp_url=d2jsp_forum_url(wanted.market),
        currencies=sorted(rates),
    )


@router.post("/watch/{watch_id}/rules")
async def add_rule(watch_id: int, session: Db, form: Annotated[RuleForm, Form()]):
    target = f"/watch/{watch_id}"
    currency = normalize_currency(form.threshold_currency)
    if currency not in await load_rates(session, None):
        return redirect(target, error=f"Unknown currency '{form.threshold_currency}'")
    session.add(
        AlertRule(
            watch_item_id=watch_id,
            metric=form.metric,
            direction=form.direction,
            threshold_amount=form.threshold_amount,
            threshold_currency=currency,
            cooldown_hours=form.cooldown_hours,
        )
    )
    await session.commit()
    return redirect(target)


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int, session: Db):
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(404, "No such rule")
    rule.enabled = not rule.enabled
    rule.armed = True
    await session.commit()
    return redirect(f"/watch/{rule.watch_item_id}")


@router.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int, session: Db):
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(404, "No such rule")
    target = f"/watch/{rule.watch_item_id}"
    await session.delete(rule)
    await session.commit()
    return redirect(target)
