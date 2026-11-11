"""diablo2.io: the item catalog, recent sales, and a second opinion on the calendar.

Only pages robots.txt allows: the category indexes, item pages and the front page. The
sold block is the last 10 sales across all markets, lagged 3 days and cached for a day,
so this is a thin, slow signal and is polled daily. It also supplies the catalog, which
is why the app works without Traderie. Show "Data courtesy of diablo2.io" wherever this
data appears; the site asks that of users of its public API.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from bs4 import BeautifulSoup, Tag

from d2_trader.currency import normalize_currency
from d2_trader.markets import UNKNOWN_VERSION, MarketKey
from d2_trader.sources.http import PoliteClient
from d2_trader.sources.types import ParsedListing

SOURCE = "diablo2io"
SITE = "https://diablo2.io"
CATEGORIES = ("uniques", "runewords", "sets", "base", "misc")
PLATFORM_TITLES = {"pc": "PC", "playstation": "playstation", "xbox": "xbox", "switch": "switch"}
MONTHS = "January February March April May June July August September October November December"
MONTH_NUMBERS = {name: i for i, name in enumerate(MONTHS.split(), start=1)}

ENDS_BANNER = re.compile(r"Ladder Season (\d+) ends on the (\d+)(?:st|nd|rd|th) of (\w+)")
STARTS_BANNER = re.compile(
    r"Ladder Season (\d+) starts on the (\d+)(?:st|nd|rd|th)(?: of)? (\w+) at (\d{2})(\d{2}) UTC"
)


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    path: str
    category: str


@dataclass(frozen=True)
class BannerDate:
    number: int
    kind: str  # "start" or "end"
    at: datetime


def parse_item_index(html: str, category: str) -> list[CatalogEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for link in soup.select("h3.z-sort-name a[href]"):
        path = str(link["href"]).split("?", 1)[0]
        if path.startswith(f"/{category}/"):
            entries[path] = CatalogEntry(link.get_text(strip=True), path, category)
    return list(entries.values())


def _market_from_flags(entry: Tag) -> MarketKey | None:
    titles = {str(flag.get("title", "")).lower() for flag in entry.select(".zi-bb")}
    if "non-ladder" in titles:
        ladder = False
    elif "ladder" in titles:
        ladder = True
    else:
        return None
    platform = next((p for t, p in PLATFORM_TITLES.items() if t in titles), None)
    if platform is None:
        return None
    mode = "hardcore" if "hardcore" in titles else "softcore"
    return MarketKey(platform, mode, ladder, UNKNOWN_VERSION)


def _sale_from(entry: Tag, item_path: str) -> ParsedListing | None:
    market = _market_from_flags(entry)
    date_tag = entry.select_one(".z-relative-date[title]")
    lots = []
    for amount in entry.select(".z-sold-amount-nu"):
        link = amount.find_next("a")
        if link is None:
            return None
        lots.append((normalize_currency(link.get_text(strip=True)), float(amount.get_text())))
    if market is None or date_tag is None or not lots:
        return None  # A free-text price, or an entry with no market: nothing to compare.
    stamp = str(date_tag["title"])
    observed = datetime.strptime(stamp, "%a %b %d, %Y %I:%M %p").replace(tzinfo=UTC)
    identity = f"{item_path}|{stamp}|{lots}|{market}".encode()
    return ParsedListing(
        source=SOURCE,
        external_id=hashlib.sha256(identity).hexdigest()[:32],
        kind="sold",
        market=market,
        alternatives=[lots],
        observed_at=observed,
        url=f"{SITE}{item_path}",
    )


def parse_sold(html: str, item_path: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "html.parser")
    sales = [_sale_from(e, item_path) for e in soup.select("section.z-nu-activetrades-sold")]
    return [s for s in sales if s]


def has_price_block(html: str) -> bool:
    """False means the markup moved, which is different from an item with no sales."""
    return "Prices and Demand" in html


def _banner_date(month: str, day: str, now: datetime, hour=0, minute=0) -> datetime | None:
    number = MONTH_NUMBERS.get(month)
    if number is None:
        return None
    # The banner gives no year, and a banner is always about the near future or past.
    candidates = [
        datetime(year, number, int(day), hour, minute, tzinfo=UTC)
        for year in (now.year - 1, now.year, now.year + 1)
    ]
    return min(candidates, key=lambda at: abs(at - now))


def parse_banner(html: str, now: datetime) -> list[BannerDate]:
    """The season dates in the front page's announcement, when it has one."""
    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())
    text = text.replace(" ,", ",")
    dates = []
    for number, day, month in ENDS_BANNER.findall(text):
        at = _banner_date(month, day, now)
        if at:
            dates.append(BannerDate(int(number), "end", at))
    for number, day, month, hour, minute in STARTS_BANNER.findall(text):
        at = _banner_date(month, day, now, int(hour), int(minute))
        if at:
            dates.append(BannerDate(int(number), "start", at))
    return dates


async def fetch_catalog(client: PoliteClient) -> list[CatalogEntry]:
    entries = []
    for category in CATEGORIES:
        response = await client.get(f"{SITE}/{category}/")
        entries += parse_item_index(response.text, category)
    return entries


async def fetch_sold(client: PoliteClient, item_path: str) -> list[ParsedListing]:
    response = await client.get(f"{SITE}{item_path}")
    if not has_price_block(response.text):
        raise ValueError(f"no price block on {item_path}: the page markup has changed")
    return parse_sold(response.text, item_path)


async def fetch_banner(client: PoliteClient, now: datetime) -> list[BannerDate]:
    return parse_banner((await client.get(f"{SITE}/")).text, now)
