"""Watchlists and their alert rules, and judging those rules against the market."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from d2_trader.alerts import DIRECTIONS, METRICS, Decision, RuleState, evaluate
from d2_trader.config import Settings
from d2_trader.currency import alternative_value, format_in, format_range
from d2_trader.markets import MarketKey
from d2_trader.models import AlertRule, Watchlist, WatchItem
from d2_trader.forum_gold import Valuation
from d2_trader.seasons import SeasonState
from d2_trader.services.calendar import current_state, load_seasons
from d2_trader.services.catalog import get_or_create_market, market_key
from d2_trader.services.fg_rates import valuation_for
from d2_trader.services.notify import Message, deliver
from d2_trader.services.pricing import MarketView, PriceQuery, market_view


async def list_watchlists(session: AsyncSession) -> list[Watchlist]:
    query = (
        select(Watchlist)
        .options(selectinload(Watchlist.entries))
        .order_by(Watchlist.created_at, Watchlist.id)
    )
    return list((await session.execute(query)).scalars().unique())


async def add_watch(
    session: AsyncSession, watchlist_id: int, item_id: int, key: MarketKey, ethereal: bool | None
) -> WatchItem:
    market = await get_or_create_market(session, key)
    query = select(WatchItem).where(
        WatchItem.watchlist_id == watchlist_id,
        WatchItem.item_id == item_id,
        WatchItem.market_id == market.id,
        WatchItem.ethereal.is_(ethereal) if ethereal is None else WatchItem.ethereal == ethereal,
    )
    existing = (await session.execute(query)).scalars().first()
    if existing:
        return existing
    watch = WatchItem(
        watchlist_id=watchlist_id, item_id=item_id, market_id=market.id, ethereal=ethereal
    )
    session.add(watch)
    await session.flush()
    await session.refresh(watch)
    return watch


async def get_watch(session: AsyncSession, watch_id: int) -> WatchItem | None:
    query = (
        select(WatchItem).where(WatchItem.id == watch_id).execution_options(populate_existing=True)
    )
    return (await session.execute(query)).scalars().first()


async def enabled_watches(session: AsyncSession) -> list[WatchItem]:
    query = select(WatchItem).order_by(WatchItem.id).execution_options(populate_existing=True)
    return list((await session.execute(query)).scalars())


def query_for(watch: WatchItem) -> PriceQuery:
    return PriceQuery(watch.item_id, market_key(watch.market), watch.ethereal)


async def view_for(
    session: AsyncSession,
    watch: WatchItem,
    state: SeasonState,
    now: datetime,
    settings: Settings,
) -> tuple[MarketView, Valuation]:
    wanted = query_for(watch)
    seasons = await load_seasons(session)
    valuation = await valuation_for(session, watch.market_id, wanted.market, seasons, now)
    stale_after = timedelta(hours=settings.ask_stale_hours)
    view = await market_view(session, wanted, state, valuation, now, stale_after)
    return view, valuation


def threshold_in_ist(rule: AlertRule, rates: dict[str, float]) -> float | None:
    """Rules keep the currency they were written in, so a rate change moves them with it."""
    return alternative_value([(rule.threshold_currency, rule.threshold_amount)], rates)


def describe_rule(rule: AlertRule) -> str:
    currency = rule.threshold_currency.title()
    return (
        f"{METRICS[rule.metric]} {DIRECTIONS[rule.direction]} {rule.threshold_amount:g} {currency}"
    )


def _rule_state(rule: AlertRule, threshold: float) -> RuleState:
    return RuleState(
        direction=rule.direction,
        threshold=threshold,
        armed=rule.armed,
        enabled=rule.enabled,
        cooldown_hours=rule.cooldown_hours,
        last_fired_at=rule.last_fired_at,
    )


def alert_message(
    watch: WatchItem, rule: AlertRule, view: MarketView, value: float, rates, now: datetime
) -> Message:
    market = market_key(watch.market)
    unit = rule.threshold_currency  # Everything in the unit the rule was written in.
    lines = [f"{describe_rule(rule)}: now {format_in(value, unit, rates)} on {market.label}."]
    fair = view.summary.fair
    if fair:
        fair_range = format_range(fair.low, fair.high, rates, unit)
        lines.append(f"Fair range {fair_range} ({view.summary.basis}, {fair.samples} samples).")
    if view.note:
        lines.append(view.note)
    return Message(
        kind="price",
        dedupe_key=f"rule:{rule.id}:{now:%Y%m%dT%H%M}",
        title=f"{watch.item.name}: alert reached",
        body=" ".join(lines),
        url=f"/watch/{watch.id}",
    )


async def judge_rule(
    rule: AlertRule, view: MarketView, rates: dict[str, float], now: datetime
) -> tuple[Decision, float | None]:
    """Decide one rule and update its stored state. Sending is the caller's business."""
    threshold = threshold_in_ist(rule, rates)
    if threshold is None:
        rule.last_reason = f"no rate for {rule.threshold_currency}"
        return Decision(False, rule.armed, rule.last_reason), None
    value = view.summary.metric(rule.metric)
    decision = evaluate(_rule_state(rule, threshold), value, now, view.window.closed)
    rule.armed = decision.armed
    rule.last_reason = decision.reason
    if decision.fire:
        rule.last_fired_at = now
    return decision, value


async def evaluate_all(session: AsyncSession, settings: Settings, now: datetime) -> int:
    """Run every rule against the current market. Returns how many fired."""
    state, _ = await current_state(session, now)
    fired = 0
    for watch in await enabled_watches(session):
        rules = [r for r in watch.rules if r.enabled]
        if not rules:
            continue
        view, valuation = await view_for(session, watch, state, now, settings)
        rates = valuation.rates
        for rule in rules:
            decision, value = await judge_rule(rule, view, rates, now)
            if decision.fire and value is not None:
                await deliver(
                    session, alert_message(watch, rule, view, value, rates, now), settings
                )
                fired += 1
    return fired
