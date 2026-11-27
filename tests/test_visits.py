"""Opening a listing through the dashboard, and the mark an opened offer then carries."""

from datetime import timedelta

from sqlalchemy import select

from d2_trader.markets import MarketKey
from d2_trader.models import Listing, ListingVisit, utcnow
from d2_trader.services.pricing import store_listings
from d2_trader.services.rune_values import rune_item
from d2_trader.services.visits import open_listing, record_visit, visited_urls
from d2_trader.sources.traderie import listing_url
from d2_trader.sources.types import ParsedListing
from d2_trader.web import routes_listings
from d2_trader.web.routes_listings import open_listing_page

URL = "https://traderie.com/diablo2resurrected/listing/1"


async def test_a_visit_is_remembered_and_a_second_moves_its_time(session):
    first = utcnow()
    await record_visit(session, URL, first)
    await record_visit(session, URL, first + timedelta(hours=1))
    [visit] = (await session.scalars(select(ListingVisit))).all()
    assert visit.url == URL
    assert visit.visited_at == first + timedelta(hours=1)


async def test_only_opened_urls_are_visited(session):
    await record_visit(session, URL, utcnow())
    assert await visited_urls(session, [URL, URL + "2", None]) == {URL}


async def test_no_urls_ask_nothing(session):
    assert await visited_urls(session, [None, ""]) == set()


async def stored(session, *listings: ParsedListing) -> list[Listing]:
    now = utcnow()
    lo = await rune_item(session, "lo")
    await store_listings(session, lo.id, list(listings), now)
    await session.flush()
    return list((await session.scalars(select(Listing).order_by(Listing.external_id))).all())


def offer(external_id: str, url: str | None, kind: str = "ask") -> ParsedListing:
    return ParsedListing(
        "traderie", external_id, kind, MarketKey(), [[("pul", 15.0)]], utcnow(), url=url
    )


async def test_opening_a_listing_records_its_page(session):
    [listing] = await stored(session, offer("1", URL))
    assert await open_listing(session, listing.id, utcnow()) == URL
    assert await visited_urls(session, [URL]) == {URL}


async def test_a_listing_without_a_page_or_id_opens_nothing(session):
    [listing] = await stored(session, offer("1", None))
    assert await open_listing(session, listing.id, utcnow()) is None
    assert await open_listing(session, listing.id + 1000, utcnow()) is None
    assert (await session.scalars(select(ListingVisit))).all() == []


def test_the_route_is_bound():
    bound = {(m, r.path): r.endpoint for r in routes_listings.router.routes for m in r.methods}
    assert bound == {("GET", "/listings/{listing_id}/open"): open_listing_page}


async def store_trade_up(sessions) -> Listing:
    """A Lo sold for 6 Ist, and one Lo asked for 15 Pul: a trade up with a page."""
    now = utcnow()
    listings = [
        ParsedListing("traderie", "s", "sold", MarketKey(), [[("ist", 6.0)]], now),
        offer("pul", listing_url("pul")),
    ]
    async with sessions() as session:
        lo = await rune_item(session, "lo")
        await store_listings(session, lo.id, listings, now)
        await session.commit()
        query = select(Listing).where(Listing.external_id == "pul")
        return (await session.scalars(query)).one()


async def test_the_open_link_redirects_to_the_listing_and_marks_it(client, sessions):
    listing = await store_trade_up(sessions)
    page = (await client.get("/trade-up")).text
    assert f'href="/listings/{listing.id}/open"' in page
    assert "&#10003; seen" not in page and '<tr class="visited">' not in page

    response = await client.get(f"/listings/{listing.id}/open", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == listing_url("pul")

    page = (await client.get("/trade-up")).text
    assert '<tr class="visited">' in page
    assert "&#10003; seen" in page


async def test_an_unknown_listing_is_not_found(client):
    response = await client.get("/listings/987654/open", follow_redirects=False)
    assert response.status_code == 404
