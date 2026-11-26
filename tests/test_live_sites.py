"""Do the parsers still understand the real sites? Run by `scripts/e2e.py`, never in the suite.

A scraper's real failure mode is a site changing its markup, and no fixture can catch
that. These fetch live pages as the collectors do, within robots.txt and at a human pace.

Two tiers. Blizzard's news feed is a public feed behind CloudFront, so it is `ci_e2e`: the
PR Gate's E2E job fetches it from a hosted runner. diablo2.io stays `local`: it sits behind
Cloudflare, which may challenge a datacenter address with an honestly named client, and
it asks for a minute between requests, so it is fetched from this workstation, nightly,
and not from a runner on every merge. A challenge is an answer (`d2_trader/sources/http.py`),
so a runner it refused would be a red no fix could honestly clear.

Traderie is absent on purpose: its terms forbid automated access, and this tier runs
unattended. So is d2jsp, whose rules forbid automation and which nothing here touches.
"""

from datetime import UTC, datetime

import pytest

from d2_trader.config import get_settings
from d2_trader.sources import blizzard, diablo2io
from d2_trader.sources.http import Pace, PoliteClient

# A runeword page, since those are the items that trade most often.
ITEM_PATH = "/runewords/harmony-t1234.html"


def client(interval: float) -> PoliteClient:
    return PoliteClient(user_agent=get_settings().user_agent, pace=Pace(interval, jitter=0.5))


@pytest.mark.ci_e2e
async def test_blizzard_still_publishes_a_readable_schedule():
    async with client(2.0) as site:
        dates, article = await blizzard.fetch_latest_schedule(site, datetime.now(UTC))
    assert article is not None, "no ladder season article on Blizzard's news page"
    assert dates, f"no schedule lines parsed from {article.url}"


@pytest.mark.local
async def test_diablo2io_item_pages_still_parse():
    async with client(2.0) as site:
        sales = await diablo2io.fetch_sold(site, ITEM_PATH)
    assert sales, f"no sales parsed from {ITEM_PATH}"
    assert all(s.market.platform for s in sales)


@pytest.mark.local
async def test_diablo2io_catalog_still_parses():
    async with client(2.0) as site:
        response = await site.get(f"{diablo2io.SITE}/runewords/")
    entries = diablo2io.parse_item_index(response.text, "runewords")
    assert len(entries) > 50, f"only {len(entries)} runewords parsed"
    assert any(e.name == "Enigma" for e in entries)
