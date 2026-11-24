"""matching router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

On-request Match Score for one Listing (FR-9, AD-9). Read-only: nothing here commits.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.modules.matching import service
from app.modules.matching.schemas import ListingMatch

router = APIRouter(tags=["matching"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}


@router.get(
    "/listings/{listing_id}/match",
    response_model=ListingMatch,
    operation_id="getListingMatch",
    responses={404: {"description": "No such Listing for this Student", **_PROBLEM}},
)
async def get_listing_match(
    listing_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ListingMatch:
    """The Match Score (0-100) of one Listing you can see, with its breakdown: matched and
    missing skills and each component's points out of its maximum. Computed now; not
    stored."""
    result = await service.match_listing(session, actor.id, listing_id)
    return ListingMatch(
        listing_id=result.listing_id, score=result.score, breakdown=result.breakdown
    )
