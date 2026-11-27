"""Opening a listing's page: through here, so the dashboard remembers it was opened."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from d2_trader.models import utcnow
from d2_trader.services.visits import open_listing
from d2_trader.web.pages import get_session

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]


@router.get("/listings/{listing_id}/open")
async def open_listing_page(listing_id: int, session: Db):
    """Record the visit and send the browser on to the listing's own page.

    It takes a stored listing's id, never a URL, so it cannot redirect anywhere the
    collector did not store.
    """
    url = await open_listing(session, listing_id, utcnow())
    if url is None:
        raise HTTPException(404, "No such listing, or it has no page")
    await session.commit()
    return RedirectResponse(url, status_code=303)
