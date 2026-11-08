"""The politeness layer. Its guarantees are what make the collectors acceptable to run."""

import httpx
import pytest

from d2_trader.sources.http import (
    Pace,
    PoliteClient,
    RobotsDenied,
    RobotsRule,
    SourceBlocked,
    is_challenge,
    parse_robots,
    robots_allows,
)

# diablo2.io's real robots.txt shape, including the group that singles out Scrapy.
D2IO_ROBOTS = """User-agent: *
Disallow: /tools/*
Disallow: /memberlist.php
Disallow: /trade/*

User-agent: Scrapy
Disallow: /
"""
UA = "d2-trader/0.1 (personal price tracker)"


def client_for(handler, **kwargs):
    defaults = {"user_agent": UA, "pace": Pace(0.0)}
    return PoliteClient(transport=httpx.MockTransport(handler), **{**defaults, **kwargs})


def site(routes, robots=""):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        status, body = routes.get(request.url.path, (404, "nope"))
        return httpx.Response(status, text=body)

    return handler


def test_a_rule_matches_from_the_start_of_the_path():
    assert RobotsRule(False, "/trade/").matches("/trade/x")
    assert not RobotsRule(False, "/trade/").matches("/x/trade/")


def test_wildcard_rule_actually_blocks():
    # The reason this module exists: urllib.robotparser would allow this.
    rules = parse_robots(D2IO_ROBOTS)
    assert not robots_allows(rules, "/trade/some-listing-t1.html")


def test_item_pages_are_allowed():
    rules = parse_robots(D2IO_ROBOTS)
    assert robots_allows(rules, "/uniques/harlequin-crest-t1.html")
    assert robots_allows(rules, "/")


def test_a_group_for_another_crawler_does_not_bind_us():
    assert robots_allows(parse_robots(D2IO_ROBOTS), "/uniques/")


def test_a_group_naming_us_replaces_the_star_group():
    text = "User-agent: *\nDisallow: /a\n\nUser-agent: d2-trader\nDisallow: /b\n"
    rules = parse_robots(text)
    assert robots_allows(rules, "/a")
    assert not robots_allows(rules, "/b")


def test_an_empty_disallow_allows_everything():
    assert robots_allows(parse_robots("User-agent: *\nDisallow:\n"), "/anything")


def test_consecutive_agent_lines_share_their_rules():
    text = "User-agent: other\nUser-agent: *\nDisallow: /x\n"
    assert not robots_allows(parse_robots(text), "/x")


def test_longest_rule_wins_and_allow_wins_a_tie():
    rules = parse_robots(
        "User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /same\nAllow: /same\n"
    )
    assert robots_allows(rules, "/a/b/c")
    assert not robots_allows(rules, "/a/c")
    assert robots_allows(rules, "/same")


def test_end_anchor_is_respected():
    rules = parse_robots("User-agent: *\nDisallow: /*.pdf$\n")
    assert not robots_allows(rules, "/doc.pdf")
    assert robots_allows(rules, "/doc.pdf.html")


def test_comments_are_ignored():
    assert not robots_allows(parse_robots("User-agent: * # all\nDisallow: /x # no\n"), "/x")


async def test_a_forbidden_path_is_never_requested():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return site({}, D2IO_ROBOTS)(request)

    async with client_for(handler) as client:
        with pytest.raises(RobotsDenied):
            await client.get("https://example.test/trade/x")
    assert seen == ["/robots.txt"]


async def test_requests_identify_this_client_honestly():
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        return site({"/x": (200, "ok")})(request)

    async with client_for(handler) as client:
        await client.get("https://example.test/x")
    assert set(agents) == {UA}


async def test_robots_is_read_once_per_site():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return site({"/x": (200, "ok")})(request)

    async with client_for(handler) as client:
        await client.get("https://example.test/x")
        await client.get("https://example.test/x")
    assert paths.count("/robots.txt") == 1


async def test_missing_robots_allows():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="ok")

    async with client_for(handler) as client:
        assert (await client.get("https://example.test/x")).text == "ok"


@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_unreadable_robots_means_stop(status):
    async with client_for(lambda r: httpx.Response(status)) as client:
        with pytest.raises(SourceBlocked):
            await client.get("https://example.test/x")


@pytest.mark.parametrize("status", [402, 403, 429, 451, 503])
async def test_a_refusal_raises_blocked_and_is_not_retried(status):
    calls = []

    def handler(request):
        if request.url.path != "/robots.txt":
            calls.append(request.url.path)
        return site({"/x": (status, "no")})(request)

    async with client_for(handler) as client:
        with pytest.raises(SourceBlocked):
            await client.get("https://example.test/x")
    assert calls == ["/x"]


async def test_a_challenge_page_counts_as_a_block_even_on_a_200():
    page = "<html><head><title>Just a moment...</title></head></html>"
    async with client_for(site({"/x": (200, page)})) as client:
        with pytest.raises(SourceBlocked):
            await client.get("https://example.test/x")


def test_is_challenge_leaves_ordinary_pages_alone():
    assert not is_challenge(httpx.Response(200, text="<title>Harmony</title>"))


async def test_an_ordinary_error_is_not_mistaken_for_a_block():
    async with client_for(site({"/x": (404, "gone")})) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get("https://example.test/x")


async def paced_waits(pace: Pace, requests: int = 2) -> list[float]:
    waits = []
    clock = [100.0]

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    async with client_for(
        site({"/x": (200, "ok")}), pace=pace, sleep=sleep, clock=lambda: clock[0]
    ) as client:
        for _ in range(requests):
            await client.get("https://example.test/x")
    return waits


async def test_requests_are_paced_to_the_minimum_interval():
    # robots then page, then page again.
    assert await paced_waits(Pace(60.0)) == [60.0, 60.0]


async def test_jitter_stretches_each_gap_by_a_random_share():
    draws = iter([0.0, 0.5, 1.0])
    waits = await paced_waits(Pace(10.0, jitter=1.0, rand=lambda: next(draws)), requests=3)
    assert waits == [10.0, 15.0, 20.0]


async def test_the_next_client_to_a_site_keeps_its_distance():
    # Regression: the rates job's fresh client hit Traderie 0.3s after the listings job's.
    waits = []
    clock = [100.0]

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    for _ in range(2):
        async with client_for(
            site({"/x": (200, "ok")}), pace=Pace(10.0), sleep=sleep, clock=lambda: clock[0]
        ) as client:
            await client.get("https://example.test/x")
    # The second client's robots.txt read waits too, though that client is brand new.
    assert waits == [10.0, 10.0, 10.0]


async def test_pacing_is_per_site():
    waits = []
    clock = [100.0]

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    async with client_for(
        site({"/x": (200, "ok")}), pace=Pace(10.0), sleep=sleep, clock=lambda: clock[0]
    ) as client:
        await client.get("https://one.test/x")
        await client.get("https://two.test/x")
    assert waits == [10.0, 10.0]  # Each site's page after its own robots.txt, nothing more.


def test_gaps_stay_between_the_minimum_and_its_jittered_maximum():
    pace = Pace(5.0, jitter=1.0)
    gaps = [pace.next_gap() for _ in range(500)]
    assert 5.0 <= min(gaps) <= max(gaps) <= 10.0
    assert max(gaps) - min(gaps) > 2.0  # It does vary.


def test_no_jitter_is_a_fixed_gap():
    assert Pace(7.0).next_gap() == 7.0


async def test_jitter_never_changes_who_the_client_says_it_is():
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        return site({"/x": (200, "ok")})(request)

    async with client_for(handler, pace=Pace(0.0, jitter=1.0)) as client:
        await client.get("https://example.test/x")
    assert set(agents) == {UA}


async def test_query_strings_are_checked_against_robots():
    robots = "User-agent: *\nDisallow: /search?secret\n"
    async with client_for(site({"/search": (200, "ok")}, robots)) as client:
        with pytest.raises(RobotsDenied):
            await client.get("https://example.test/search", params={"secret": "1"})
