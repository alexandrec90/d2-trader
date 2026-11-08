import json

import httpx

from d2_trader.config import Settings
from d2_trader.services.notify import (
    Message,
    deliver,
    discord_payload,
    mark_all_read,
    push,
    recent_notifications,
    unread_count,
)

MESSAGE = Message("price", "key-1", "Shako: alert reached", "Below 2 Ber.", "/watch/1")
ALL_CHANNELS = {"discord_webhook_url": "https://hook.test/d", "ntfy_url": "https://ntfy.test/t"}


def configured(**fields) -> Settings:
    return Settings(_env_file=None, database_url="x", **fields)


def recorder(status=200):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    return calls, httpx.MockTransport(handler)


def test_discord_text_carries_title_body_and_link():
    content = discord_payload(MESSAGE)["content"]
    assert "**Shako: alert reached**" in content
    assert "Below 2 Ber." in content
    assert "/watch/1" in content


async def test_nothing_configured_sends_nothing():
    calls, transport = recorder()
    assert await push(MESSAGE, configured(), transport) == {}
    assert calls == []


async def test_each_configured_channel_is_sent():
    calls, transport = recorder()
    results = await push(MESSAGE, configured(**ALL_CHANNELS), transport)
    assert results == {"discord": "sent", "ntfy": "sent"}
    assert json.loads(calls[0].content)["content"].startswith("**Shako")
    assert calls[1].headers["title"] == "Shako: alert reached"


async def test_a_title_the_header_cannot_carry_does_not_break_sending():
    calls, transport = recorder()
    message = Message("price", "k", "Tal Rasha\N{RIGHT SINGLE QUOTATION MARK}s Set", "body")
    await push(message, configured(ntfy_url="https://ntfy.test/t"), transport)
    assert calls[0].headers["title"] == "Tal Rasha?s Set"


async def test_a_failing_channel_is_reported_and_does_not_stop_the_other():
    def handler(request):
        return httpx.Response(500 if "hook" in str(request.url) else 200)

    results = await push(MESSAGE, configured(**ALL_CHANNELS), httpx.MockTransport(handler))
    assert results == {"discord": "failed: HTTPStatusError", "ntfy": "sent"}


async def test_an_unreachable_channel_is_reported():
    def handler(request):
        raise httpx.ConnectError("down")

    results = await push(
        MESSAGE, configured(ntfy_url="https://ntfy.test/t"), httpx.MockTransport(handler)
    )
    assert results == {"ntfy": "failed: ConnectError"}


async def test_delivery_stores_the_message_and_its_outcome(session):
    _calls, transport = recorder()
    settings = configured(discord_webhook_url="https://hook.test/d")
    assert await deliver(session, MESSAGE, settings, transport) is True
    [row] = await recent_notifications(session)
    assert row.title == MESSAGE.title
    assert row.delivery == {"discord": "sent"}


async def test_the_same_key_is_only_ever_delivered_once(session):
    calls, transport = recorder()
    settings = configured(discord_webhook_url="https://hook.test/d")
    await deliver(session, MESSAGE, settings, transport)
    assert await deliver(session, MESSAGE, settings, transport) is False
    assert len(calls) == 1


async def test_read_state_tracks_the_inbox(session):
    await deliver(session, MESSAGE, configured())
    assert await unread_count(session) == 1
    await mark_all_read(session)
    await session.flush()
    assert await unread_count(session) == 0
