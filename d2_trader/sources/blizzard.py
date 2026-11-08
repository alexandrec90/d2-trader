"""Blizzard's news site: the one authoritative source for when a ladder season runs.

Blizzard publishes each season's schedule in an article 1-2 weeks ahead, in a stable
format: "Ladder Season 15 Ends: Monday, November 9, 11 a.m. PST". Nothing here depends
on a third-party tracker, so a tracker going away costs nothing but the cross-check.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from d2_trader.sources.http import PoliteClient

NEWS_URL = "https://news.blizzard.com/en-us/feed/diablo-2-resurrected"
ARTICLE_ROOT = "https://news.blizzard.com/en-us"
ARTICLE_LINK = re.compile(
    r'href="[^"]*?(/article/(\d+)/diablo-ii-resurrected-ladder-season-(\d+)(-[a-z0-9-]*)?)"'
)
MONTHS = "January February March April May June July August September October November December"
MONTH_NUMBERS = {name: i for i, name in enumerate(MONTHS.split(), start=1)}
ZONE_OFFSETS = {"PST": -8, "PDT": -7, "UTC": 0, "GMT": 0}
SCHEDULE_LINE = re.compile(
    r"Ladder Season (?P<number>\d+)\s+(?P<kind>Ends|Starts)\s*:\s*"
    r"(?:[A-Z][a-z]+day,\s*)?(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?,?\s*"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap])\.?m\.?\s*"
    r"(?P<zone>PST|PDT|UTC|GMT)"
)
PUBLISHED = re.compile(r'"datePublished":"([^"]+)"')


@dataclass(frozen=True)
class ArticleRef:
    season: int
    url: str


@dataclass(frozen=True)
class ScheduleDate:
    number: int
    kind: str  # "start" or "end"
    at: datetime


def season_articles(html: str) -> list[ArticleRef]:
    """Schedule articles, newest season first. "Has concluded" notes hold no schedule."""
    refs: dict[int, ArticleRef] = {}
    for path, _id, number, suffix in ARTICLE_LINK.findall(html):
        if suffix and "concluded" in suffix:
            continue
        refs.setdefault(int(number), ArticleRef(int(number), f"{ARTICLE_ROOT}{path}"))
    return [refs[n] for n in sorted(refs, reverse=True)]


def _published(html: str) -> datetime | None:
    match = PUBLISHED.search(html)
    return datetime.fromisoformat(match.group(1).replace("Z", "+00:00")) if match else None


def _schedule_datetime(line: re.Match, published: datetime | None, now: datetime) -> datetime:
    hour = int(line["hour"]) % 12 + (12 if line["meridiem"] == "p" else 0)
    zone = timezone(timedelta(hours=ZONE_OFFSETS[line["zone"]]))
    anchor = published or now
    year = int(line["year"] or anchor.year)
    month, day, minute = MONTH_NUMBERS[line["month"]], int(line["day"]), int(line["minute"] or 0)
    at = datetime(year, month, day, hour, minute, tzinfo=zone)
    # Written in December about January: a schedule never points far behind its article.
    if not line["year"] and at < anchor - timedelta(days=60):
        at = at.replace(year=year + 1)
    return at


def parse_schedule(html: str, now: datetime) -> list[ScheduleDate]:
    """Every "Ladder Season N Ends/Starts: <when>" line in an article."""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    text = " ".join(text.replace("\xa0", " ").split())
    published = _published(html)
    found = []
    for line in SCHEDULE_LINE.finditer(text):
        if line["month"] in MONTH_NUMBERS:
            kind = "end" if line["kind"] == "Ends" else "start"
            at = _schedule_datetime(line, published, now)
            found.append(ScheduleDate(int(line["number"]), kind, at))
    return found


async def fetch_latest_schedule(
    client: PoliteClient, now: datetime
) -> tuple[list[ScheduleDate], ArticleRef | None]:
    """The schedule from the newest season article, which carries both boundary dates."""
    articles = season_articles((await client.get(NEWS_URL)).text)
    if not articles:
        return [], None
    latest = articles[0]
    return parse_schedule((await client.get(latest.url)).text, now), latest
