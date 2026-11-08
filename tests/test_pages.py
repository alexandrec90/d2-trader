from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text
from starlette.requests import Request

from d2_trader.seasons import Phase
from d2_trader.web.pages import PHASE_LABELS, get_session, humanize_delta, render

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def fake_request(sessions, query: bytes = b"") -> Request:
    app = SimpleNamespace(state=SimpleNamespace(sessions=sessions))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "app": app}
    return Request({**scope, "query_string": query, "router": None})


async def test_each_request_gets_a_working_session(sessions):
    async for session in get_session(fake_request(sessions)):
        assert (await session.execute(text("select 1"))).scalar_one() == 1


async def test_render_passes_flash_messages_to_the_page(sessions, client):
    request = fake_request(sessions, b"error=nope")
    async with sessions() as session:
        response = await render(request, session, "notifications.html", items=[])
    assert b"nope" in response.body


def test_future_and_past_read_differently():
    assert humanize_delta(NOW + timedelta(days=3, hours=4), NOW) == "in 3d 4h"
    assert humanize_delta(NOW - timedelta(hours=2, minutes=5), NOW) == "2h 5m ago"


def test_short_spans_drop_the_larger_units():
    assert humanize_delta(NOW + timedelta(minutes=7), NOW) == "in 7m"


def test_an_unknown_date_says_so():
    assert humanize_delta(None, NOW) == "unknown"


def test_every_phase_has_a_label():
    assert set(PHASE_LABELS) == set(Phase)
