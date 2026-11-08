from datetime import UTC, datetime

import httpx
import pytest

from d2_trader.markets import UNKNOWN_VERSION
from d2_trader.sources.diablo2io import (
    BannerDate,
    fetch_banner,
    fetch_catalog,
    fetch_sold,
    has_price_block,
    parse_banner,
    parse_item_index,
    parse_sold,
)
from d2_trader.sources.http import Pace, PoliteClient

PATH = "/runewords/harmony-t1234.html"
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def sale(items, flags, stamp="Wed May 13, 2026 9:22 am"):
    amounts = "".join(
        f'<span class="z-blue z-sold-amount-nu">{quantity} </span>'
        f'<span class="ajax_catch"><a class="ajax_link" href="/misc/x.html">'
        f'<span class="zi"></span>{name}</a></span>'
        for quantity, name in items
    )
    marks = "".join(f'<span class="zi zi-bb zi-{f.lower()}" title="{f}"></span>' for f in flags)
    return (
        f'<section class="z-nu-activetrades-sold">{amounts}<span class="z-grey">{marks}'
        f'<span class="z-relative-date" title="{stamp}">5 months ago</span></span></section>'
    )


def page(*sales):
    return f"<h2>Prices and Demand</h2>{''.join(sales)}"


def test_a_sale_is_parsed_with_its_market():
    [listing] = parse_sold(page(sale([(1, "Fal")], ["Non-Ladder", "PC"])), PATH)
    assert listing.alternatives == [[("fal", 1.0)]]
    assert listing.kind == "sold"
    assert listing.market.ladder is False
    assert listing.market.mode == "softcore"
    assert listing.market.platform == "PC"
    assert listing.market.game_version == UNKNOWN_VERSION
    assert listing.observed_at == datetime(2026, 5, 13, 9, 22, tzinfo=UTC)
    assert listing.url == f"https://diablo2.io{PATH}"


def test_hardcore_and_ladder_flags_are_read():
    [listing] = parse_sold(page(sale([(1, "Pul")], ["Ladder", "Hardcore", "PC"])), PATH)
    assert listing.market.ladder is True
    assert listing.market.mode == "hardcore"


def test_console_flags_are_read():
    [listing] = parse_sold(page(sale([(1, "Pul")], ["Non-Ladder", "Switch"])), PATH)
    assert listing.market.platform == "switch"


def test_a_multi_item_price_is_one_bundle():
    [listing] = parse_sold(
        page(sale([(1, "Um"), (10, "Perfect Gems")], ["Non-Ladder", "PC"])), PATH
    )
    assert listing.alternatives == [[("um", 1.0), ("perfect gems", 10.0)]]


def test_a_free_text_price_is_skipped():
    # Real entry: "Smile", with no amount and no item link.
    assert parse_sold(page(sale([], ["Non-Ladder", "PC"])), PATH) == []


def test_a_sale_with_no_ladder_flag_is_skipped_rather_than_guessed():
    assert parse_sold(page(sale([(1, "Pul")], ["PC"])), PATH) == []


def test_a_sale_with_no_platform_is_skipped_rather_than_guessed():
    assert parse_sold(page(sale([(1, "Pul")], ["Ladder"])), PATH) == []


def test_ids_are_stable_across_scrapes_and_differ_between_sales():
    html = page(sale([(1, "Pul")], ["Ladder", "PC"]), sale([(2, "Pul")], ["Ladder", "PC"]))
    first, second = parse_sold(html, PATH)
    assert first.external_id != second.external_id
    assert parse_sold(html, PATH)[0].external_id == first.external_id


def test_the_same_sale_on_two_items_is_two_records():
    html = page(sale([(1, "Pul")], ["Ladder", "PC"]))
    assert parse_sold(html, PATH)[0].external_id != parse_sold(html, "/other")[0].external_id


def test_item_index_reads_names_and_dedupes():
    link = '<h3 class="z-sort-name"><a href="/uniques/justice-t766.html?sid=abc">Justice\'s Edge</a></h3>'
    entries = parse_item_index(link + link, "uniques")
    assert [(e.name, e.path) for e in entries] == [("Justice's Edge", "/uniques/justice-t766.html")]


def test_item_index_ignores_links_to_other_categories():
    link = '<h3 class="z-sort-name"><a href="/base/x-t1.html">Base</a></h3>'
    assert parse_item_index(link, "uniques") == []


BANNER = (
    "<p>Ladder Season 15 ends on the 9th of November , and Ladder Season 16 starts on the "
    "14th November at 0100 UTC .</p>"
)


def test_banner_gives_both_boundaries():
    assert parse_banner(BANNER, NOW) == [
        BannerDate(15, "end", datetime(2026, 11, 9, tzinfo=UTC)),
        BannerDate(16, "start", datetime(2026, 11, 14, 1, tzinfo=UTC)),
    ]


def test_banner_year_follows_the_nearest_date():
    late_december = datetime(2026, 12, 28, tzinfo=UTC)
    text = "Ladder Season 17 starts on the 3rd of January at 0100 UTC"
    assert parse_banner(text, late_december)[0].at.year == 2027


def test_no_banner_means_no_dates():
    assert parse_banner("<p>Nothing about seasons.</p>", NOW) == []


def test_an_unreadable_month_is_dropped():
    assert parse_banner("Ladder Season 15 ends on the 9th of Smarch", NOW) == []


def test_price_block_detection():
    assert has_price_block(page())
    assert not has_price_block("<html>redesigned</html>")


def transport(pages):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    return httpx.MockTransport(handler)


def client(pages):
    return PoliteClient(user_agent="t", pace=Pace(0), transport=transport(pages))


async def test_fetch_sold_refuses_a_page_that_lost_its_price_block():
    # Otherwise a redesign would read as "this item stopped selling".
    async with client({PATH: "<html>redesigned</html>"}) as site:
        with pytest.raises(ValueError, match="markup"):
            await fetch_sold(site, PATH)


async def test_fetch_sold_reads_a_page():
    html = page(sale([(1, "Pul")], ["Ladder", "PC"]))
    async with client({PATH: html}) as site:
        assert len(await fetch_sold(site, PATH)) == 1


async def test_fetch_catalog_walks_every_category():
    index = '<h3 class="z-sort-name"><a href="/misc/ber-t1.html">Ber</a></h3>'
    async with client({"/misc/": index}) as site:
        assert [e.name for e in await fetch_catalog(site)] == ["Ber"]


async def test_fetch_banner_reads_the_front_page():
    async with client({"/": BANNER}) as site:
        assert len(await fetch_banner(site, NOW)) == 2
