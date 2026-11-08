from d2_trader.services.calendar import load_seasons
from d2_trader.web.app import create_app


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
