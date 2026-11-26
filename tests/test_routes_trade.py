"""The Trade up page: offers into a higher rune, and the holdings that filter them."""

import re
from urllib.parse import unquote

from d2_trader.markets import MarketKey
from d2_trader.models import utcnow
from d2_trader.services.holdings import FIELD, load_holdings, save_holdings
from d2_trader.services.pricing import store_listings
from d2_trader.services.rune_values import rune_item
from d2_trader.sources.types import ParsedListing
from d2_trader.web import routes_trade
from d2_trader.web.routes_trade import (
    form_market,
    save_holdings_form,
    trade_up_page,
)

MARKET_FIELDS = {"platform": "PC", "mode": "softcore", "ladder": "true", "version": "rotw"}


def test_the_page_and_its_form_are_routed():
    bound = {(m, r.path): r.endpoint for r in routes_trade.router.routes for m in r.methods}
    assert bound == {
        ("GET", "/trade-up"): trade_up_page,
        ("POST", "/trade-up/holdings"): save_holdings_form,
    }


def test_a_form_names_its_market_as_a_query_does():
    assert form_market(MARKET_FIELDS) == MarketKey()
    fields = {"platform": "PC", "mode": "hardcore", "ladder": "false", "version": "nonsense"}
    assert form_market(fields) == MarketKey(mode="hardcore", ladder=False)
    assert form_market({}) == MarketKey()


async def store_offers(sessions):
    """A Lo sold for 6 Ist, and three Lo asks: 15 Pul (up), 4 Vex (up), a Ber (down)."""
    now = utcnow()
    listings = [
        ParsedListing("traderie", "s", "sold", MarketKey(), [[("ist", 6.0)]], now),
        ParsedListing("traderie", "pul", "ask", MarketKey(), [[("pul", 15.0)]], now),
        ParsedListing("traderie", "vex", "ask", MarketKey(), [[("vex", 2.0)]], now),
        ParsedListing("traderie", "down", "ask", MarketKey(), [[("ber", 1.0)]], now),
    ]
    async with sessions() as session:
        lo = await rune_item(session, "lo")
        await store_listings(session, lo.id, listings, now)
        await session.commit()


async def hold(sessions, held, market=MarketKey()):
    async with sessions() as session:
        await save_holdings(session, market, held)
        await session.commit()


def table(page):
    start = page.index('<table class="asks trade-ups">')
    return page[start : page.index("</table>", start)]


def paid(page):
    return re.findall(r"<td>1 Lo</td>\s*<td>([^<]+)</td>", table(page))


async def test_with_nothing_saved_every_trade_up_is_listed(client, sessions):
    await store_offers(sessions)
    page = (await client.get("/trade-up")).text
    assert sorted(paid(page)) == ["15 Pul", "2 Vex"]
    assert "1 Ber" not in table(page)
    assert "save what you hold" in page


async def test_saved_holdings_keep_only_what_they_can_pay_for(client, sessions):
    await store_offers(sessions)
    await hold(sessions, {"pul": 15, "um": 10})
    page = (await client.get("/trade-up")).text
    assert paid(page) == ["15 Pul"]
    assert "show every trade up" in page
    every = (await client.get("/trade-up?all=1")).text
    assert sorted(paid(every)) == ["15 Pul", "2 Vex"]
    assert "only what I can pay for" in every


async def test_holdings_too_small_for_any_offer_say_so(client, sessions):
    await store_offers(sessions)
    await hold(sessions, {"pul": 10})
    page = (await client.get("/trade-up")).text
    assert '<table class="asks trade-ups">' not in page
    assert "that your holdings can pay for right now" in page


async def test_the_form_shows_what_is_saved(client, sessions):
    await hold(sessions, {"pul": 15, "perfect amethyst": 2})
    page = (await client.get("/trade-up")).text
    assert re.search(r'name="held:pul" value="15"', page)
    assert re.search(r'name="held:perfect amethyst" value="2"', page)
    assert re.search(r'name="held:ber" value=""', page)
    # The goods family with something held opens; one with nothing stays folded.
    assert re.search(r'<details class="holding-group" open[^>]*>\s*<summary>Gems</summary>', page)
    assert re.search(r'<details class="holding-group" >\s*<summary>Keys</summary>', page)


async def test_a_group_the_page_opens_does_not_pause_live_updates(client, sessions):
    """Regression: the Runes group is always open, and any open <details> paused the live
    redraw, so the page sat at "update waiting" until reloaded by hand."""
    await hold(sessions, {"perfect amethyst": 2})
    page = (await client.get("/trade-up")).text
    opened = re.findall(r'<details class="holding-group" ([^>]*)>\s*<summary>([^<]+)<', page)
    by_title = {title: attrs for attrs, title in opened}
    assert by_title["Runes"] == "open data-keeps-live"
    assert by_title["Gems"] == "open data-keeps-live"
    assert by_title["Keys"] == ""  # Folded: one opened by hand still pauses.


async def test_saving_holdings_tells_an_open_page_to_redraw(client):
    before = (await client.get("/api/live")).json()["version"]
    await client.post("/trade-up/holdings", data={**MARKET_FIELDS, f"{FIELD}pul": "15"})
    assert (await client.get("/api/live")).json()["version"] != before


async def test_the_form_saves_and_comes_back_to_its_market(client, sessions):
    form = {**MARKET_FIELDS, "ladder": "false", f"{FIELD}pul": "15", f"{FIELD}vex": "4"}
    form[f"{FIELD}ber"] = ""
    response = await client.post("/trade-up/holdings", data=form)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/trade-up?platform=PC&mode=softcore&ladder=false&version=rotw")
    assert "Holdings saved" in unquote(location)
    async with sessions() as session:
        assert await load_holdings(session, MarketKey(ladder=False)) == {"pul": 15, "vex": 4}
        assert await load_holdings(session, MarketKey()) == {}


async def test_a_bad_count_saves_nothing_and_says_why(client, sessions):
    await hold(sessions, {"pul": 15})
    form = {**MARKET_FIELDS, f"{FIELD}pul": "3", f"{FIELD}vex": "1.5"}
    location = (await client.post("/trade-up/holdings", data=form)).headers["location"]
    assert "Nothing saved: Vex: '1.5' is not a whole number" in unquote(location)
    async with sessions() as session:
        assert await load_holdings(session, MarketKey()) == {"pul": 15}


async def test_the_saved_message_is_shown(client):
    page = (await client.get("/trade-up?message=Holdings+saved")).text
    assert 'role="status">Holdings saved<' in page


async def test_the_form_carries_the_market(client):
    page = (await client.get("/trade-up?mode=hardcore&ladder=false")).text
    form = page[page.index('<form method="post" action="/trade-up/holdings"') :]
    form = form[: form.index("</form>")]
    for name, value in [("mode", "hardcore"), ("ladder", "false"), ("version", "rotw")]:
        assert f'name="{name}" value="{value}"' in form
