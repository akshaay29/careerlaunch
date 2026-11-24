"""matching router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

The Match Score of one Listing with its breakdown, stored or computed on request (FR-9,
AD-9), and of a batch of Listings (search results, Story 3.4), both read-only; the count-only
"Why this score" event; and the ranked feed (Story 3.3, FR-10), which commits only when a
Student's first feed open queues their first recompute.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.core.jobs import drain_priority_after_response
from app.modules.matching import service
from app.modules.matching.schemas import (
    Feed,
    FeedItem,
    ListingMatch,
    MatchScoreItem,
    MatchScores,
)

router = APIRouter(tags=["matching"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}

# The shared after-response priority drain (``core.jobs``); a module global so tests can
# replace it.
drain_priority = drain_priority_after_response


@router.get(
    "/listings/{listing_id}/match",
    response_model=ListingMatch,
    operation_id="getListingMatch",
    responses={
        401: {"description": "Not signed in", **_PROBLEM},
        404: {"description": "No such Listing for this Student", **_PROBLEM},
    },
)
async def get_listing_match(
    listing_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ListingMatch:
    """The Match Score (0-100) of one Listing you can see, with its breakdown: matched and
    missing skills and each component's points out of its maximum. My stored score when I
    have one (``stored``); otherwise computed now and not stored (``on_request``)."""
    result = await service.match_listing(session, actor.id, listing_id)
    return ListingMatch(
        listing_id=result.listing_id,
        score=result.score,
        breakdown=result.breakdown,
        source=result.source,
    )


@router.post(
    "/listings/{listing_id}/match/viewed",
    status_code=204,
    response_class=Response,
    operation_id="recordBreakdownViewed",
    responses={
        401: {"description": "Not signed in", **_PROBLEM},
        404: {"description": "No such Listing for this Student", **_PROBLEM},
    },
)
async def record_breakdown_viewed(
    listing_id: uuid.UUID, actor: CurrentActor, session: Session
) -> Response:
    """Count that I opened "Why this score" for a Listing I can see. Only the count is
    recorded: no Listing, score or skill."""
    await service.record_breakdown_opened(session, actor.id, listing_id)
    await session.commit()
    return Response(status_code=204)


@router.get(
    "/matches",
    response_model=MatchScores,
    operation_id="getMatchScores",
    responses={
        401: {"description": "Not signed in", **_PROBLEM},
        422: {
            "description": "More than 50 distinct ids, or an id is not a UUID",
            **_PROBLEM,
        },
    },
)
async def get_match_scores(
    actor: CurrentActor,
    session: Session,
    ids: Annotated[
        list[uuid.UUID] | None, Query(max_length=service.MAX_BATCH_RAW_IDS)
    ] = None,
) -> MatchScores:
    """My Match Scores (no breakdowns) for up to 50 distinct Listings, e.g. one page of
    search results. Duplicates count once (at most 200 values in all); Listings I cannot see, expired or unknown ones are left
    out. Stored scores are used where I have them; the rest are computed now and not
    stored."""
    items = await service.match_scores(session, actor.id, ids or [])
    return MatchScores(
        items=[
            MatchScoreItem(listing_id=i.listing_id, score=i.score, source=i.source)
            for i in items
        ]
    )


@router.get(
    "/feed",
    response_model=Feed,
    operation_id="getFeed",
    responses={
        401: {"description": "Not signed in", **_PROBLEM},
        422: {"description": "The cursor or limit is invalid", **_PROBLEM},
    },
)
async def get_feed(
    actor: CurrentActor,
    session: Session,
    background: BackgroundTasks,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    show_below_threshold: bool = False,
) -> Feed:
    """My Listings ranked by Match Score, best first. Only Listings I can see that have not
    expired; those under my threshold only with ``show_below_threshold``. Before my scores are
    stored, my top matches are scored now (``provisional``)."""
    page = await service.feed(
        session,
        actor.id,
        limit=limit,
        cursor=cursor,
        show_below_threshold=show_below_threshold,
    )
    if page.queued:
        await session.commit()
        background.add_task(drain_priority)
    return Feed(
        state=page.state,
        threshold=page.threshold,
        hidden_below_threshold=page.hidden_below_threshold,
        items=[
            FeedItem.from_listing(
                e.item.listing,
                e.item.source_label,
                score=e.score,
                below_threshold=e.below_threshold,
            )
            for e in page.items
        ],
        next_cursor=page.next_cursor,
    )
