from sqlalchemy import select

from d2_trader.models import Item, Listing, Market, Notification, SourceRun, utcnow
from d2_trader.web import routes_admin
from d2_trader.web.routes_admin import (
    FgMarket,
    clear_rate,
    import_page,
    import_prices,
    notifications,
    parse_moment,
    rates_page,
    read_notifications,
    save_rate,
    save_season,
    seasons_page,
    fg_sections,
    live,
    sources_page,
)

IMPORT_FORM = {
    "platform": "PC",
    "mode": "softcore",
    "ladder": "yes",
    "game_version": "rotw",
    "kind": "sold",
    "ethereal": "any",
}


def test_each_url_reaches_its_handler():
    bound = {
        (method, route.path): route.endpoint
        for route in routes_admin.router.routes
        for method in route.methods
    }
    assert bound == {
        ("GET", "/notifications"): notifications,
        ("POST", "/notifications/read"): read_notifications,
        ("GET", "/api/live"): live,
        ("GET", "/seasons"): seasons_page,
        ("POST", "/seasons"): save_season,
        ("GET", "/rates"): rates_page,
        ("POST", "/rates"): save_rate,
        ("POST", "/rates/{currency}/clear"): clear_rate,
        ("GET", "/import"): import_page,
        ("POST", "/import"): import_prices,
        ("GET", "/sources"): sources_page,
    }


def test_form_times_are_read_as_utc():
    assert parse_moment("2026-11-14T01:00").utcoffset().total_seconds() == 0
    assert parse_moment("") is None


async def test_inbox_empty_state(client):
    assert "Nothing yet" in (await client.get("/notifications")).text


async def seed_notification(sessions):
    async with sessions() as session, session.begin():
        session.add(
            Notification(
                kind="price", dedupe_key="k", title="Shako: alert reached", body="b", delivery={}
            )
        )


async def test_inbox_lists_and_clears(client, sessions):
    await seed_notification(sessions)
    assert "Shako: alert reached" in (await client.get("/notifications")).text
    assert (await client.get("/api/live")).json()["count"] == 1
    await client.post("/notifications/read")
    after = (await client.get("/api/live")).json()
    assert (after["count"], after["latest"]) == (0, None)


async def test_live_api_names_the_latest(client, sessions):
    await seed_notification(sessions)
    latest = (await client.get("/api/live")).json()["latest"]
    assert latest["title"] == "Shako: alert reached"


async def test_live_version_moves_when_data_does(client, sessions):
    before = (await client.get("/api/live")).json()["version"]
    assert (await client.get("/api/live")).json()["version"] == before
    await seed_notification(sessions)
    assert (await client.get("/api/live")).json()["version"] != before


async def add_runes(sessions):
    async with sessions() as session, session.begin():
        session.add_all([Item(name=n, category="misc") for n in ("Ber", "Jah", "Ist")])


async def test_a_rune_price_list_is_imported_line_by_line(client, sessions):
    await add_runes(sessions)
    text = "Ber 150fg\nJah - 200 fg\n2 ist 20fg\nbump\nZod 900fg"
    form = {**IMPORT_FORM, "paste_as": "runes", "kind": "ask", "text": text}
    location = (await client.post("/import", data=form)).headers["location"]
    assert "Stored%203%20rune%20prices" in location
    assert "skipped%202" in location  # "bump", and Zod, which the catalog lacks here.
    async with sessions() as session:
        rows = (await session.execute(select(Listing))).scalars().all()
    assert sorted(r.quantity for r in rows) == [1, 1, 2]


async def test_rates_page_shows_the_measured_fg_rate(client, sessions):
    await add_runes(sessions)
    form = {**IMPORT_FORM, "paste_as": "runes", "text": "Ber 125fg\nIst 10fg"}
    await client.post("/import", data=form)
    page = (await client.get("/rates")).text
    assert "1 Ist = 10 fg" in page  # Seeds: Ber 12.5 Ist, so both runes say 10 fg an Ist.
    assert "95% CI" in page
    assert "1 Ber = 125 fg" in page


async def test_a_rune_list_keeps_the_market_it_was_pasted_for(client, sessions):
    """Regression: the form's list type and the market's mode shared one field name."""
    await add_runes(sessions)
    form = {**IMPORT_FORM, "mode": "hardcore", "paste_as": "runes", "text": "Ber 150fg"}
    await client.post("/import", data=form)
    async with sessions() as session:
        listing = (await session.execute(select(Listing))).scalars().one()
        market = await session.get(Market, listing.market_id)
    assert market.mode == "hardcore"


def test_an_fg_section_carries_the_markets_own_ber_value():
    section = FgMarket("PC", None, None, 14.0)
    assert section.ber_ist == 14.0


async def test_rates_page_says_how_to_start_measuring(client):
    assert "Not measured yet" in (await client.get("/rates")).text


async def test_fg_sections_list_only_markets_with_rune_prices(client, sessions):
    async with sessions() as session:
        assert await fg_sections(session, utcnow()) == []


async def test_seasons_page_shows_the_calendar_and_how_resets_work(client):
    page = await client.get("/seasons")
    assert "Season 16" in page.text
    assert "How resets change prices here" in page.text


async def test_a_manual_date_is_saved_and_beats_a_sync(client):
    response = await client.post("/seasons", data={"number": "16", "starts_at": "2026-11-20T01:00"})
    assert "message=" in response.headers["location"]
    page = await client.get("/seasons")
    assert "Nov 20, 2026 01:00" in page.text
    assert "manual" in page.text


async def test_rates_page_lists_rates_and_flags_forum_gold(client):
    page = await client.get("/rates")
    assert "Ber" in page.text
    assert "fg" in page.text


async def test_a_rate_can_be_set_and_cleared(client):
    await client.post("/rates", data={"currency": "FG", "value_ist": "0.25"})
    page = await client.get("/rates")
    assert "manual" in page.text
    await client.post("/rates/fg/clear")
    assert "manual" not in (await client.get("/rates")).text.split("<tbody>")[1]


async def test_ist_cannot_be_redefined(client):
    response = await client.post("/rates", data={"currency": "Ist", "value_ist": "2"})
    assert "error=" in response.headers["location"]


async def test_import_page_links_the_right_forums(client):
    page = await client.get("/import")
    assert "forum.php?f=271" in page.text


async def test_import_stores_prices_and_reports_skips(client, sessions):
    async with sessions() as session, session.begin():
        session.add(Item(name="Shako", category="uniques"))
    response = await client.post(
        "/import", data={"item": "Shako", "text": "2 ber\nbump", **IMPORT_FORM}
    )
    assert "Stored%201" in response.headers["location"]
    assert "skipped%201%3A%20bump" in response.headers["location"]  # Says which line.
    async with sessions() as session:
        assert (await session.execute(select(Listing))).first() is not None


async def test_import_refuses_an_unknown_item(client):
    response = await client.post("/import", data={"item": "Nope", "text": "2 ber", **IMPORT_FORM})
    assert "error=" in response.headers["location"]


async def test_import_refuses_an_unknown_kind(client, sessions):
    async with sessions() as session, session.begin():
        session.add(Item(name="Shako", category="uniques"))
    form = {**IMPORT_FORM, "kind": "stolen"}
    response = await client.post("/import", data={"item": "Shako", "text": "2 ber", **form})
    assert response.status_code == 422


async def test_sources_page_shows_health_and_the_traderie_reason(client, sessions):
    async with sessions() as session, session.begin():
        session.add(
            SourceRun(job="traderie", status="disabled", detail="off", finished_at=utcnow())
        )
    page = await client.get("/sources")
    assert "disabled" in page.text
    assert "Terms of Service" in page.text
