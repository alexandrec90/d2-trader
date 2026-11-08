"""Telling the operator. The dashboard inbox always works; push channels are optional."""

from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.config import Settings
from d2_trader.models import Notification, utcnow


@dataclass(frozen=True)
class Message:
    kind: str
    dedupe_key: str
    title: str
    body: str
    url: str | None = None


def discord_payload(message: Message) -> dict:
    link = f"\n{message.url}" if message.url else ""
    return {"content": f"**{message.title}**\n{message.body}{link}"}


async def push(
    message: Message, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, str]:
    """Send to each configured channel. One channel failing never stops the other."""
    results: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        if settings.discord_webhook_url:
            results["discord"] = await _send(
                client.post(settings.discord_webhook_url, json=discord_payload(message))
            )
        if settings.ntfy_url:
            results["ntfy"] = await _send(
                client.post(
                    settings.ntfy_url,
                    content=message.body.encode(),
                    headers={"Title": message.title.encode("ascii", "replace").decode()},
                )
            )
    return results


async def _send(request) -> str:
    try:
        response = await request
        response.raise_for_status()
    except httpx.HTTPError as error:
        return f"failed: {type(error).__name__}"
    return "sent"


async def deliver(
    session: AsyncSession,
    message: Message,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Store and push a message once. Returns False if its key was already used."""
    statement = (
        insert(Notification)
        .values(
            kind=message.kind,
            dedupe_key=message.dedupe_key,
            title=message.title,
            body=message.body,
            url=message.url,
            delivery={},
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
        .returning(Notification.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    if inserted is None:
        return False
    row = await session.get_one(Notification, inserted)
    row.delivery = await push(message, settings, transport)
    await session.flush()
    return True


async def recent_notifications(session: AsyncSession, limit: int = 100) -> list[Notification]:
    query = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars())


async def unread_count(session: AsyncSession) -> int:
    query = select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    return (await session.execute(query)).scalar_one()


async def mark_all_read(session: AsyncSession) -> None:
    rows = await session.execute(select(Notification).where(Notification.read_at.is_(None)))
    now = utcnow()
    for row in rows.scalars():
        row.read_at = now
