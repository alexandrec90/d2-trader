import pytest

from d2_trader.services.calendar import load_seasons
from d2_trader.web.app import create_app


async def test_the_dashboard_reports_the_release_it_started_on(client, monkeypatch):
    monkeypatch.setenv("D2_TRADER_RELEASE", "sha:code")
    assert (await client.get("/api/release")).json() == {"release": "sha:code"}
    monkeypatch.delenv("D2_TRADER_RELEASE")
    assert (await client.get("/api/release")).json() == {"release": ""}


async def test_static_files_are_served(client):
    assert (await client.get("/static/app.css")).status_code == 200
    assert (await client.get("/static/app.js")).status_code == 200


async def test_the_stylesheet_hides_the_idle_crosshair(client):
    # Regression: `hidden` does nothing to an SVG element by itself, so it sat at x=0.
    assert ".cross[hidden]" in (await client.get("/static/app.css")).text


async def test_hidden_always_hides_whatever_the_element_styles_say(client):
    # Regression: `.badge { display: inline-block }` beat `hidden`, so it showed "0".
    css = (await client.get("/static/app.css")).text
    assert "[hidden] { display: none !important; }" in css


async def test_the_footer_credits_diablo2io(client):
    assert "Data courtesy of" in (await client.get("/")).text


async def test_every_page_links_a_tab_icon_that_is_served(client):
    assert 'rel="icon" href="/static/favicon.svg?v=' in (await client.get("/")).text
    icon = await client.get("/static/favicon.svg")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/seasons",
        "/import",
        "/runes",
        "/trade-up",
        "/currencies",
        "/rates",
        "/sources",
        "/notifications",
    ],
)
async def test_only_the_open_tab_is_marked_current(client, path):
    page = (await client.get(path)).text
    nav = page[page.index('<nav aria-label="Main">') : page.index("</nav>")]
    assert f'<a href="{path}" aria-current="page">' in nav
    assert nav.count("aria-current") == 1


async def test_only_a_section_opened_by_hand_pauses_live_updates(client):
    """A `<details>` the page renders open carries `data-keeps-live`; the busy check
    must skip it, or such a page never redraws."""
    script = (await client.get("/static/app.js")).text
    assert 'main details[open]:not([data-keeps-live])' in script
    assert 'querySelector("main details[open]")' not in script


async def test_the_stylesheet_marks_the_open_tab(client):
    assert ".top nav a[aria-current]" in (await client.get("/static/app.css")).text


async def test_api_docs_are_not_published(sessions, settings):
    app = create_app(sessions, settings)
    assert app.docs_url is None
    assert app.redoc_url is None


async def test_startup_seeds_the_calendar(sessions, settings):
    app = create_app(sessions, settings)
    async with app.router.lifespan_context(app):
        pass
    async with sessions() as session:
        assert await load_seasons(session)
