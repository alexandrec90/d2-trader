"""What every page shares: the session, the template context, the formatters."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.alerts import DIRECTIONS, METRICS
from d2_trader.currency import format_like, format_range, format_value
from d2_trader.markets import GAME_VERSIONS, MODES, PLATFORMS, VERSION_LABELS, MarketKey
from d2_trader.models import utcnow
from d2_trader.seasons import Phase
from d2_trader.services.notify import unread_count

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

PHASE_LABELS = {
    Phase.LAUNCH: "Launch",
    Phase.EARLY: "Early season",
    Phase.MID: "Mid season",
    Phase.LATE: "Season ending",
    Phase.OFFSEASON: "Between seasons",
    Phase.UNKNOWN: "Calendar unknown",
}


def humanize_delta(target: datetime | None, now: datetime) -> str:
    """'in 3d 4h' or '2h ago'. A reset's distance matters more than its timestamp."""
    if target is None:
        return "unknown"
    seconds = int((target - now).total_seconds())
    days, remainder = divmod(abs(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        text = f"{days}d {hours}h"
    elif hours:
        text = f"{hours}h {minutes}m"
    else:
        text = f"{minutes}m"
    return f"in {text}" if seconds >= 0 else f"{text} ago"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A session per request. Handlers that write commit before they redirect."""
    async with request.app.state.sessions() as session:
        yield session


async def render(
    request: Request, session: AsyncSession, name: str, **context: object
) -> HTMLResponse:
    context.setdefault("error", request.query_params.get("error"))
    context.setdefault("message", request.query_params.get("message"))
    return templates.TemplateResponse(
        request,
        name,
        {
            "unread": await unread_count(session),
            "now": utcnow(),
            "phase_labels": PHASE_LABELS,
            "platforms": PLATFORMS,
            "modes": MODES,
            "game_versions": {k: VERSION_LABELS[k] for k in GAME_VERSIONS},
            "metrics": METRICS,
            "directions": DIRECTIONS,
            "default_market": MarketKey(),
            **context,
        },
    )


templates.env.globals["format_value"] = format_value
templates.env.globals["format_range"] = format_range
templates.env.globals["format_like"] = format_like
templates.env.globals["humanize"] = humanize_delta
