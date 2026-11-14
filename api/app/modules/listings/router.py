"""listings router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Browsing the shared Listing pool (FR-6, FR-7): a page of Listings visible to the Student
(the pool plus their own private Listings), not expired, newest first; and one Listing by id
(404 for anything not visible). Read-only: nothing here commits.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import skills
from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.modules.listings import service
from app.modules.listings.schemas import ListingList, ListingOut, ListingSkill

router = APIRouter(tags=["listings"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}


def _out(item: service.BrowseItem) -> ListingOut:
    listing = item.listing
    return ListingOut(
        id=listing.id,
        title=listing.title,
        company=listing.company,
        location=listing.location,
        job_type=listing.job_type,  # type: ignore[arg-type]
        posted_at=listing.posted_at,
        deadline=listing.deadline,
        url=listing.url,
        source=listing.source,
        source_label=item.source_label,
        skills=[ListingSkill(id=s, label=skills.label(s)) for s in listing.skills],
        first_seen_at=listing.first_seen_at,
    )


@router.get(
    "/listings",
    response_model=ListingList,
    operation_id="listListings",
    responses={422: {"description": "The cursor or limit is invalid", **_PROBLEM}},
)
async def list_listings(
    actor: CurrentActor,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ListingList:
    """Listings you can see (the shared pool and your own private ones), newest first."""
    page = await service.browse(session, actor.id, limit=limit, cursor=cursor)
    return ListingList(items=[_out(i) for i in page.items], next_cursor=page.next_cursor)


@router.get(
    "/listings/{listing_id}",
    response_model=ListingOut,
    operation_id="getListing",
    responses={404: {"description": "No such Listing for this Student", **_PROBLEM}},
)
async def get_listing(listing_id: uuid.UUID, actor: CurrentActor, session: Session) -> ListingOut:
    return _out(await service.get_listing(session, actor.id, listing_id))
