"""The only way this project reaches another site, and how it stays a polite one.

Every request honours robots.txt, keeps to a randomised per-site pace and names itself
honestly. A refusal is an answer: `SourceBlocked` stops the run and the worker backs off.
There are no retries, no rotated identities, and no attempt at a challenge page, by design.
"""

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

# 402 is what Traderie's edge answers a client it dislikes; 451 is a legal block.
BLOCK_STATUSES = {401, 402, 403, 407, 429, 451, 503}
CHALLENGE_MARKERS = ("<title>Just a moment...</title>", "challenge-platform")
AGENT_NAME = "d2-trader"
# When each site last heard from this process, whichever client asked. Two jobs that run
# back to back each open a client, and a site sees one visitor either way.
LAST_REQUEST: dict[str, float] = {}


class SourceBlocked(Exception):
    """The site refused this client. Stop for now, and do not look for a way around it."""


class RobotsDenied(Exception):
    """robots.txt forbids this URL, so it was never requested."""


@dataclass(frozen=True)
class RobotsRule:
    allow: bool
    pattern: str

    def matches(self, path: str) -> bool:
        body = re.escape(self.pattern).replace(r"\*", ".*")
        if body.endswith(r"\$"):
            body = body[:-2] + "$"
        return re.match(body, path) is not None


def _robots_groups(text: str) -> dict[str, list[RobotsRule]]:
    """Each named agent's rules. Consecutive `User-agent` lines share a group."""
    groups: dict[str, list[RobotsRule]] = {}
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        field, _, value = raw.split("#", 1)[0].partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            agents = [] if in_rules else agents
            in_rules = False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in ("allow", "disallow"):
            in_rules = True
            rules = [RobotsRule(field == "allow", value)] if value else []
            for name in agents:
                groups[name] += rules
    return groups


def parse_robots(text: str, agent: str = AGENT_NAME) -> list[RobotsRule]:
    """The rules that bind `agent`: its own group if named, otherwise `*`.

    `urllib.robotparser` is not an option. It compares paths literally, so a rule like
    diablo2.io's `Disallow: /trade/*` would never match anything and would allow it all.
    """
    groups = _robots_groups(text)
    named = [rules for name, rules in groups.items() if name != "*" and name in agent.lower()]
    return named[0] if named else groups.get("*", [])


def robots_allows(rules: list[RobotsRule], path: str) -> bool:
    """The longest matching rule decides, and Allow wins a tie (RFC 9309)."""
    matches = [r for r in rules if r.matches(path)]
    if not matches:
        return True
    return max(matches, key=lambda r: (len(r.pattern), r.allow)).allow


@dataclass(frozen=True)
class Pace:
    """How long to leave between two requests to one site.

    Each gap is `seconds` stretched by a random part of `jitter`: 0.5 means anywhere from
    1x to 1.5x. That spreads requests out so they never arrive on a fixed beat. It is not
    a disguise: the client still names itself, and still stops when a site says no.
    """

    seconds: float
    jitter: float = 0.0
    rand: Callable[[], float] = random.random

    def next_gap(self) -> float:
        return self.seconds * (1 + self.jitter * self.rand())


def is_challenge(response: httpx.Response) -> bool:
    head = response.text[:4096]
    return any(marker in head for marker in CHALLENGE_MARKERS)


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        pace: Pace,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            transport=transport,
            follow_redirects=True,
            timeout=25,
        )
        self._pace_rule = pace
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, list[RobotsRule]] = {}

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def _pace(self, host: str) -> None:
        last = LAST_REQUEST.get(host)
        if last is not None:
            wait = self._pace_rule.next_gap() - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        LAST_REQUEST[host] = self._clock()

    async def _rules_for(self, origin: str) -> list[RobotsRule]:
        if origin not in self._robots:
            await self._pace(urlsplit(origin).netloc)
            response = await self._http.get(f"{origin}/robots.txt")
            if response.status_code in BLOCK_STATUSES or response.status_code >= 500:
                # RFC 9309: an unreadable robots.txt means assume everything is forbidden.
                raise SourceBlocked(f"{origin}/robots.txt answered {response.status_code}")
            self._robots[origin] = (
                parse_robots(response.text) if response.status_code == 200 else []
            )
        return self._robots[origin]

    async def get(self, url: str, params: dict | None = None) -> httpx.Response:
        target = httpx.URL(url, params=params) if params else httpx.URL(url)
        parts = urlsplit(str(target))
        path = f"{parts.path}?{parts.query}" if parts.query else parts.path
        rules = await self._rules_for(f"{parts.scheme}://{parts.netloc}")
        if not robots_allows(rules, path):
            raise RobotsDenied(f"robots.txt forbids {path} on {parts.netloc}")

        await self._pace(parts.netloc)
        response = await self._http.get(target)
        if response.status_code in BLOCK_STATUSES:
            raise SourceBlocked(f"{parts.netloc} answered {response.status_code} for {path}")
        if is_challenge(response):
            raise SourceBlocked(f"{parts.netloc} served a bot challenge for {path}")
        response.raise_for_status()
        return response
