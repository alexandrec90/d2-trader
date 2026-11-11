from datetime import UTC, datetime

import httpx

from d2_trader.sources.blizzard import (
    ArticleRef,
    fetch_latest_schedule,
    parse_schedule,
    season_articles,
)
from d2_trader.sources.http import Pace, PoliteClient

NOW = datetime(2026, 11, 6, tzinfo=UTC)
# The schedule lines exactly as Blizzard's Season 16 article renders them, tags included.
ARTICLE = """
<script type="application/ld+json">{"datePublished":"2026-10-31T00:06:00Z"}</script>
<h2>Ladder Season 16 Schedule</h2>
<ul>
<li><strong>Ladder Season 15 Ends:</strong> Monday, November 9, 11 a.m. PST</li>
<li>Season 16 Patch begins rolling out: Monday, November 9</li>
<li><strong>Ladder Season 16 Starts</strong>: Friday, November 13, 5 p.m. PST</li>
</ul>
"""
NEWS = """
<a href="https://news.blizzard.com/en-us/article/24246296/diablo-ii-resurrected-ladder-season-13-has-concluded">x</a>
<a href="/en-us/article/24296140/diablo-ii-resurrected-ladder-season-15-now-live">x</a>
<a href="/en-us/article/24316567/diablo-ii-resurrected-ladder-season-16">x</a>
<a href="/en-us/article/24316567/diablo-ii-resurrected-ladder-season-16">again</a>
<a href="/en-us/article/24243863/rain-annihilation-in-reign-of-the-warlock">x</a>
"""


def test_schedule_reads_both_boundaries_in_utc():
    dates = {(d.number, d.kind): d.at for d in parse_schedule(ARTICLE, NOW)}
    assert dates[(15, "end")] == datetime(2026, 11, 9, 19, tzinfo=UTC)
    assert dates[(16, "start")] == datetime(2026, 11, 14, 1, tzinfo=UTC)


def test_a_patch_line_is_not_a_season_date():
    assert len(parse_schedule(ARTICLE, NOW)) == 2


def test_daylight_time_is_respected():
    text = "<p>Ladder Season 14 Starts: Friday, May 22, 5 p.m. PDT</p>"
    [date] = parse_schedule(text, datetime(2026, 5, 1, tzinfo=UTC))
    assert date.at == datetime(2026, 5, 23, 0, tzinfo=UTC)


def test_noon_and_midnight_convert_correctly():
    noon = parse_schedule("<p>Ladder Season 9 Ends: May 1, 12 p.m. UTC</p>", NOW)[0]
    midnight = parse_schedule("<p>Ladder Season 9 Ends: May 1, 12 a.m. UTC</p>", NOW)[0]
    assert (noon.at.hour, midnight.at.hour) == (12, 0)


def test_minutes_and_an_explicit_year_are_understood():
    [date] = parse_schedule("<p>Ladder Season 9 Ends: May 1, 2027, 10:30 a.m. UTC</p>", NOW)
    assert date.at == datetime(2027, 5, 1, 10, 30, tzinfo=UTC)


def test_a_january_date_written_in_december_lands_in_the_next_year():
    html = (
        '{"datePublished":"2026-12-20T00:00:00Z"}'
        "<p>Ladder Season 17 Starts: Friday, January 8, 5 p.m. PST</p>"
    )
    assert parse_schedule(html, NOW)[0].at.year == 2027


def test_a_non_breaking_space_does_not_hide_a_date():
    text = "<p>Ladder Season 16 Starts:\xa0Friday, November\xa013, 5 p.m. PST</p>"
    assert len(parse_schedule(text, NOW)) == 1


def test_an_article_without_a_schedule_yields_nothing():
    assert parse_schedule("<p>Ladder Season 15 Now Live</p>", NOW) == []


def test_articles_are_newest_first_without_concluded_notes():
    refs = season_articles(NEWS)
    assert [r.season for r in refs] == [16, 15]
    assert refs[0] == ArticleRef(
        16,
        "https://news.blizzard.com/en-us/article/24316567/diablo-ii-resurrected-ladder-season-16",
    )


def test_no_season_articles_is_empty():
    assert season_articles("<a href='/en-us/article/1/other'>x</a>") == []


def client(pages):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages[request.url.path])

    return PoliteClient(user_agent="t", pace=Pace(0), transport=httpx.MockTransport(handler))


async def test_fetch_follows_the_newest_article():
    pages = {
        "/en-us/feed/diablo-2-resurrected": NEWS,
        "/en-us/article/24316567/diablo-ii-resurrected-ladder-season-16": ARTICLE,
    }
    async with client(pages) as site:
        dates, article = await fetch_latest_schedule(site, NOW)
    assert article.season == 16
    assert len(dates) == 2


async def test_fetch_with_no_articles_says_so():
    async with client({"/en-us/feed/diablo-2-resurrected": ""}) as site:
        assert await fetch_latest_schedule(site, NOW) == ([], None)
