"""The pages, driven the way a browser drives them, against a real database."""

from datetime import timedelta

import pytest_asyncio
from sqlalchemy import select

from d2_trader.models import AlertRule, Item, Listing, Watchlist, WatchItem, utcnow
from d2_trader.services.pricing import store_listings
from d2_trader.services.rates import set_manual_rate
from d2_trader.web import routes_watch
from d2_trader.web.routes_watch import (
    WatchRow,
    add_item,
    add_rule,
    create_watchlist,
    dashboard,
    delete_rule,
    delete_watch,
    delete_watchlist,
    items_api,
    redirect,
    toggle_rule,
    watch_detail,
)

MARKET_FORM = {
    "platform": "PC",
    "mode": "softcore",
    "ladder": "yes",
    "game_version": "rotw",
    "ethereal": "any",
}


@pytest_asyncio.fixture
async def watch_id(client, sessions):
    async with sessions() as session, session.begin():
        session.add(Item(name="Shako", category="uniques"))
    await client.post("/watchlists", data={"name": "Mine"})
    async with sessions() as session:
        watchlist = (await session.execute(select(Watchlist))).scalars().one()
    await client.post(f"/watchlists/{watchlist.id}/items", data={"item": "shako", **MARKET_FORM})
    async with sessions() as session:
        return (await session.execute(select(WatchItem.id))).scalars().one()


def test_each_url_reaches_its_handler():
    bound = {
        (method, route.path): route.endpoint
        for route in routes_watch.router.routes
        for method in route.methods
    }
    assert bound == {
        ("GET", "/"): dashboard,
        ("GET", "/api/items"): items_api,
        ("POST", "/watchlists"): create_watchlist,
        ("POST", "/watchlists/{watchlist_id}/delete"): delete_watchlist,
        ("POST", "/watchlists/{watchlist_id}/items"): add_item,
        ("POST", "/watch/{watch_id}/delete"): delete_watch,
        ("GET", "/watch/{watch_id}"): watch_detail,
        ("POST", "/watch/{watch_id}/rules"): add_rule,
        ("POST", "/rules/{rule_id}/toggle"): toggle_rule,
        ("POST", "/rules/{rule_id}/delete"): delete_rule,
    }


def test_a_row_carries_what_the_dashboard_shows():
    assert {"watch", "market_label", "view", "rates"} == set(WatchRow.__dataclass_fields__)


def test_redirect_carries_a_message_safely():
    response = redirect("/x", error="a b&c")
    assert response.headers["location"] == "/x?error=a%20b%26c"
    assert response.status_code == 303


async def test_dashboard_shows_the_season_and_an_empty_state(client):
    page = await client.get("/")
    assert page.status_code == 200
    assert "Ladder Season" in page.text
    assert "Start a watchlist" in page.text


async def test_dashboard_lists_a_watched_item(client, watch_id):
    page = await client.get("/")
    assert "Shako" in page.text
    assert f"/watch/{watch_id}" in page.text


async def test_a_blank_watchlist_name_is_refused(client):
    response = await client.post("/watchlists", data={"name": "  "})
    assert "error=" in response.headers["location"]


async def test_an_unknown_item_is_refused_with_a_reason(client, sessions):
    await client.post("/watchlists", data={"name": "Mine"})
    async with sessions() as session:
        watchlist = (await session.execute(select(Watchlist))).scalars().one()
    response = await client.post(
        f"/watchlists/{watchlist.id}/items", data={"item": "Nope", **MARKET_FORM}
    )
    assert "error=" in response.headers["location"]


async def test_item_search_answers_as_json(client, watch_id):
    response = await client.get("/api/items", params={"q": "sha"})
    assert response.json() == [{"name": "Shako", "category": "uniques"}]


async def test_watch_page_shows_no_data_honestly(client, watch_id):
    page = await client.get(f"/watch/{watch_id}")
    assert page.status_code == 200
    assert "Not enough data" in page.text
    assert "Not enough history" in page.text


async def test_watch_page_with_data_draws_the_chart(client, watch_id, sessions, make_listing):
    now = utcnow()
    async with sessions() as session, session.begin():
        item_id = (await session.execute(select(Item.id))).scalars().one()
        listings = [make_listing(str(i), 12, observed_at=now - timedelta(days=i)) for i in range(6)]
        await store_listings(session, item_id, listings, now)
    page = await client.get(f"/watch/{watch_id}")
    assert "<svg" in page.text
    assert "Median sold" in page.text


async def test_scraped_text_is_escaped_on_the_page(client, watch_id, sessions, make_listing):
    hostile = "<script>alert(1)</script>"
    async with sessions() as session, session.begin():
        item_id = (await session.execute(select(Item.id))).scalars().one()
        listing = make_listing(
            "x", 2, currency=hostile, observed_at=utcnow(), url="https://example.test/"
        )
        await store_listings(session, item_id, [listing], utcnow())
    page = await client.get(f"/watch/{watch_id}")
    assert hostile not in page.text.lower()
    assert "&lt;script&gt;" in page.text.lower()


async def test_a_missing_watch_is_a_404(client):
    assert (await client.get("/watch/99999")).status_code == 404


def rule_form(**overrides):
    fields = {
        "metric": "lowest_ask",
        "direction": "at_or_below",
        "threshold_amount": "2",
        "threshold_currency": "Ber Rune",
        "cooldown_hours": "12",
    }
    return {**fields, **overrides}


async def test_a_rule_can_be_added_and_shown(client, watch_id):
    await client.post(f"/watch/{watch_id}/rules", data=rule_form())
    assert "Lowest ask at or below 2 Ber" in (await client.get(f"/watch/{watch_id}")).text


async def test_an_unknown_currency_is_refused(client, watch_id):
    response = await client.post(
        f"/watch/{watch_id}/rules", data=rule_form(threshold_currency="doubloons")
    )
    assert "error=" in response.headers["location"]


async def test_a_currency_added_on_the_rates_page_can_be_used_in_a_rule(client, watch_id, sessions):
    """Regression: rules were checked against the seed table, so these were refused."""
    async with sessions() as session, session.begin():
        await set_manual_rate(session, "annihilus", 60.0)
    await client.post(
        f"/watch/{watch_id}/rules",
        data=rule_form(threshold_currency="Annihilus", threshold_amount="1"),
    )
    assert "Lowest ask at or below 1 Annihilus" in (await client.get(f"/watch/{watch_id}")).text


async def test_an_unknown_metric_is_refused(client, watch_id):
    response = await client.post(f"/watch/{watch_id}/rules", data=rule_form(metric="vibes"))
    assert response.status_code == 422


async def test_an_unknown_market_is_refused(client, sessions):
    await client.post("/watchlists", data={"name": "Mine"})
    async with sessions() as session:
        watchlist = (await session.execute(select(Watchlist))).scalars().one()
    form = {"item": "x", **MARKET_FORM, "mode": "softcoreish"}
    assert (await client.post(f"/watchlists/{watchlist.id}/items", data=form)).status_code == 422


async def test_a_zero_threshold_is_rejected(client, watch_id):
    response = await client.post(f"/watch/{watch_id}/rules", data=rule_form(threshold_amount="0"))
    assert response.status_code == 422


async def test_a_rule_pauses_and_resumes(client, watch_id, sessions):
    await client.post(f"/watch/{watch_id}/rules", data=rule_form())
    async with sessions() as session:
        rule_id = (await session.execute(select(AlertRule.id))).scalars().one()
    await client.post(f"/rules/{rule_id}/toggle")
    async with sessions() as session:
        assert (await session.get(AlertRule, rule_id)).enabled is False
    await client.post(f"/rules/{rule_id}/toggle")
    async with sessions() as session:
        rule = await session.get(AlertRule, rule_id)
        await session.refresh(rule)
        assert rule.enabled is True


async def test_a_rule_can_be_deleted(client, watch_id, sessions):
    await client.post(f"/watch/{watch_id}/rules", data=rule_form())
    async with sessions() as session:
        rule_id = (await session.execute(select(AlertRule.id))).scalars().one()
    await client.post(f"/rules/{rule_id}/delete")
    async with sessions() as session:
        assert (await session.execute(select(AlertRule))).first() is None


async def test_rule_actions_on_a_missing_rule_are_404(client):
    assert (await client.post("/rules/99999/toggle")).status_code == 404
    assert (await client.post("/rules/99999/delete")).status_code == 404


async def test_a_watch_can_be_removed(client, watch_id, sessions):
    await client.post(f"/watch/{watch_id}/delete")
    async with sessions() as session:
        assert (await session.execute(select(WatchItem))).first() is None


async def test_a_list_can_be_deleted(client, watch_id, sessions):
    async with sessions() as session:
        watchlist = (await session.execute(select(Watchlist))).scalars().one()
    await client.post(f"/watchlists/{watchlist.id}/delete")
    assert "Start a watchlist" in (await client.get("/")).text


async def test_listings_survive_a_watch_being_removed(client, watch_id, sessions, make_listing):
    """Unwatching should not throw away price history that took weeks to collect."""
    async with sessions() as session, session.begin():
        item_id = (await session.execute(select(Item.id))).scalars().one()
        await store_listings(session, item_id, [make_listing("a", 2)], utcnow())
    await client.post(f"/watch/{watch_id}/delete")
    async with sessions() as session:
        assert (await session.execute(select(Listing))).first() is not None
