"""listings router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Browsing the shared Listing pool (FR-6, FR-7): a page of Listings visible to the Student
(the pool plus their own private Listings), not expired, newest first; and one Listing by id
(404 for anything not visible). Also the Student's collection usage (Story 2.4). Read-only:
nothing here commits.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.modules.listings import service
from app.modules.listings.schemas import (
    ListingList,
    ListingOut,
    ListingSearchResults,
    ProviderUsage,
    ProviderUsageCaps,
    ProviderUsageMonth,
    ProviderUsageRun,
    ProviderUsageStatus,
)

router = APIRouter(tags=["listings"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}


def _out(item: service.BrowseItem) -> ListingOut:
    return ListingOut.from_listing(item.listing, item.source_label)


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


# Declared before `/listings/{listing_id}` so "search" is not read as an id.
@router.get(
    "/listings/search",
    response_model=ListingSearchResults,
    operation_id="searchListings",
    responses={422: {"description": "The query, limit or offset is invalid", **_PROBLEM}},
)
async def search_listings(
    actor: CurrentActor,
    session: Session,
    q: Annotated[str, Query(max_length=service.SEARCH_MAX_QUERY_CHARS)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=service.SEARCH_MAX_OFFSET)] = 0,
) -> ListingSearchResults:
    """Keyword search over the Listings you can see (the shared pool and your own private
    ones), not expired, best match first. All words must match (as prefixes); close
    misspellings still match. ``q`` needs 2 to 100 characters with letters or digits."""
    page = await service.search(session, actor.id, query=q, limit=limit, offset=offset)
    return ListingSearchResults(items=[_out(i) for i in page.items], next_offset=page.next_offset)


@router.get(
    "/listings/{listing_id}",
    response_model=ListingOut,
    operation_id="getListing",
    responses={404: {"description": "No such Listing for this Student", **_PROBLEM}},
)
async def get_listing(listing_id: uuid.UUID, actor: CurrentActor, session: Session) -> ListingOut:
    return _out(await service.get_listing(session, actor.id, listing_id))


def _usd(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


@router.get("/provider-usage", response_model=ProviderUsage, operation_id="getProviderUsage")
async def get_provider_usage(actor: CurrentActor, session: Session) -> ProviderUsage:
    """My collection status, spend caps, this month's spend and latest runs. Never run ids,
    search parameters or tokens."""
    usage = await service.provider_usage(session, actor.id)
    return ProviderUsage(
        status=ProviderUsageStatus(
            status=usage.status.status,
            reason=usage.status.reason,
            exhausted_until=usage.status.exhausted_until,
        ),
        caps=ProviderUsageCaps(
            per_run_usd=float(usage.caps.per_run_usd),
            monthly_usd=float(usage.caps.monthly_usd),
            is_default=usage.caps.is_default,
        ),
        month=ProviderUsageMonth(
            spent_usd=float(usage.month.spent),
            committed_usd=float(usage.month.committed),
            monthly_cap_usd=float(usage.caps.monthly_usd),
        ),
        runs=[
            ProviderUsageRun(
                created_at=run.created_at,
                status=run.status,  # type: ignore[arg-type]
                error_kind=run.error_kind,
                estimated_cost_usd=_usd(run.estimated_cost_usd),
                cost_usd=_usd(run.cost_usd),
                items=run.items,
                ingested=run.ingested,
            )
            for run in usage.runs
        ],
    )
