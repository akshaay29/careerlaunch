"""tracker router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Saving a Listing as an Application and tracking its Status (FR-16, Story 4.1). Another
Student's Application is always a 404. Routers commit; the service never does.
"""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.core.jobs import drain_priority_after_response
from app.modules.tracker import service
from app.modules.tracker.schemas import (
    ApplicationForListing,
    ApplicationHistory,
    ApplicationOut,
    ApplicationStatus,
    ApplicationUpdate,
    ExternalJobIn,
    SavedListing,
    SavedListings,
    SaveListingIn,
    StatusChange,
    TrackerItemOut,
    TrackerPageOut,
    TransitionIn,
    UpcomingDeadlineOut,
    UpcomingDeadlines,
)

router = APIRouter(tags=["tracker"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}
_UNAUTHORIZED = {401: {"description": "Not signed in", **_PROBLEM}}
_NOT_FOUND = {404: {"description": "No such Application for this Student", **_PROBLEM}}

# The shared after-response priority drain (``core.jobs``); a module global so tests can
# replace it.
drain_priority = drain_priority_after_response


@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=201,
    operation_id="saveListing",
    responses={
        200: {"description": "Already saved: the existing Application", "model": ApplicationOut},
        **_UNAUTHORIZED,
        404: {"description": "No such Listing for this Student, or it expired", **_PROBLEM},
    },
)
async def save_listing(
    body: SaveListingIn, actor: CurrentActor, session: Session, response: Response
) -> ApplicationOut:
    """Save a Listing you can see as an Application in Status Saved (201). Saving it again
    returns the same Application unchanged (200)."""
    view, created = await service.save_listing(session, actor.id, body.listing_id)
    await session.commit()
    if not created:
        response.status_code = 200
    return ApplicationOut.from_view(view)


# Declared before `/applications/{application_id}` routes (a POST there is not one, but keep
# the literal paths first).
@router.post(
    "/applications/manual",
    response_model=ApplicationOut,
    status_code=201,
    operation_id="addExternalJob",
    responses={
        200: {
            "description": "You already added this link: the existing Application",
            "model": ApplicationOut,
        },
        **_UNAUTHORIZED,
        422: {
            "description": "No title and no link, an invalid link, text over its limit, a "
            "deadline before 2000-01-01, a job that can't be added, or the limit of added "
            "jobs reached",
            **_PROBLEM,
        },
    },
)
async def add_external_job(
    body: ExternalJobIn,
    actor: CurrentActor,
    session: Session,
    response: Response,
    background: BackgroundTasks,
) -> ApplicationOut:
    """Add a job you found on another site and track it (201): it is visible only to you,
    scored like any other Listing and saved as an Application in Saved. The link is stored so
    you can open it later; it is never visited. Adding the same link again returns that
    Application unchanged (200)."""
    view, created = await service.add_external_job(
        session,
        actor.id,
        url=body.url,
        title=body.title,
        company=body.company,
        location=body.location,
        job_type=body.job_type,
        deadline=body.deadline,
        description=body.description,
    )
    await session.commit()
    if created:
        background.add_task(drain_priority)
    else:
        response.status_code = 200
    return ApplicationOut.from_view(view)


@router.get(
    "/applications",
    response_model=TrackerPageOut,
    operation_id="listApplications",
    responses={
        **_UNAUTHORIZED,
        422: {
            "description": "An unknown Status, a company filter over 100 characters, a start "
            "date after the end date, or an invalid cursor or limit",
            **_PROBLEM,
        },
    },
)
async def list_applications(
    actor: CurrentActor,
    session: Session,
    status: Annotated[list[ApplicationStatus] | None, Query(max_length=12)] = None,
    company: Annotated[str | None, Query(max_length=service.MAX_COMPANY_CHARS)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    date_by: Literal["saved", "applied"] = "saved",
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE)] = service.DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> TrackerPageOut:
    """Your Applications, most recently saved first. Filters combine: ``status`` (repeat
    for any of several), ``company`` (case-insensitive part of the company name), and an
    inclusive ``date_from``/``date_to`` range on the save date in your time zone
    (``date_by=saved``) or on the applied date (``date_by=applied``; Applications without one
    are left out). ``total`` counts every match."""
    page = await service.list_applications(
        session,
        actor.id,
        statuses=status or [],
        company=company,
        date_from=date_from,
        date_to=date_to,
        date_by=date_by,
        limit=limit,
        cursor=cursor,
    )
    return TrackerPageOut(
        items=[TrackerItemOut.from_item(i) for i in page.items],
        total=page.total,
        next_cursor=page.next_cursor,
    )


@router.get(
    "/applications/deadlines",
    response_model=UpcomingDeadlines,
    operation_id="listUpcomingDeadlines",
    responses={**_UNAUTHORIZED, 422: {"description": "``days`` is not 1 to 180", **_PROBLEM}},
)
async def list_upcoming_deadlines(
    actor: CurrentActor,
    session: Session,
    days: Annotated[
        int, Query(ge=1, le=service.MAX_DEADLINE_DAYS)
    ] = service.DEFAULT_DEADLINE_DAYS,
) -> UpcomingDeadlines:
    """Your Saved, Applied and Interview Applications whose deadline falls from today to
    today + ``days`` (your time zone), soonest first, at most 20."""
    upcoming = await service.upcoming_deadlines(session, actor.id, days=days)
    return UpcomingDeadlines(items=[UpcomingDeadlineOut.from_upcoming(u) for u in upcoming])


@router.get(
    "/applications/by-listings",
    response_model=SavedListings,
    operation_id="getApplicationsForListings",
    responses={
        **_UNAUTHORIZED,
        422: {
            "description": "More than 200 ids, more than 50 distinct ids, or an id is not "
            "a UUID",
            **_PROBLEM,
        },
    },
)
async def get_applications_for_listings(
    actor: CurrentActor,
    session: Session,
    ids: Annotated[list[uuid.UUID] | None, Query(max_length=200)] = None,
) -> SavedListings:
    """Which of the given Listings you have saved, with the Application ids. ``ids`` takes at
    most 200 raw values and at most 50 distinct ids (duplicates are ignored). Unsaved
    Listings are left out."""
    pairs = await service.applications_for_listings(session, actor.id, ids or [])
    return SavedListings(
        items=[SavedListing(listing_id=lid, application_id=aid) for lid, aid in pairs]
    )


@router.get(
    "/applications/by-listing/{listing_id}",
    response_model=ApplicationForListing,
    operation_id="getApplicationForListing",
    responses=_UNAUTHORIZED,
)
async def get_application_for_listing(
    listing_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ApplicationForListing:
    """The id of your Application for a Listing, or null when you have not saved it."""
    return ApplicationForListing(
        application_id=await service.application_for_listing(session, actor.id, listing_id)
    )


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationOut,
    operation_id="getApplication",
    responses={**_UNAUTHORIZED, **_NOT_FOUND},
)
async def get_application(
    application_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ApplicationOut:
    """One of your Applications, with the Statuses it may move to next."""
    return ApplicationOut.from_view(
        await service.get_application(session, actor.id, application_id)
    )


@router.patch(
    "/applications/{application_id}",
    response_model=ApplicationOut,
    operation_id="updateApplication",
    responses={
        **_UNAUTHORIZED,
        **_NOT_FOUND,
        422: {
            "description": "Notes over 4,000 characters, a future applied date, or a Resume "
            "that is not yours",
            **_PROBLEM,
        },
    },
)
async def update_application(
    application_id: uuid.UUID, body: ApplicationUpdate, actor: CurrentActor, session: Session
) -> ApplicationOut:
    """Change the notes, the applied date or the Resume version used. Only the fields you
    send change; the Status never does."""
    fields = ("notes", "applied_on", "resume_id")
    kwargs = {name: getattr(body, name) for name in fields if name in body.model_fields_set}
    view = await service.update_details(session, actor.id, application_id, **kwargs)
    await session.commit()
    return ApplicationOut.from_view(view)


@router.post(
    "/applications/{application_id}/status",
    response_model=ApplicationOut,
    operation_id="transitionApplication",
    responses={
        **_UNAUTHORIZED,
        **_NOT_FOUND,
        409: {
            "description": "The move is not allowed; ``allowed`` lists the next Statuses",
            **_PROBLEM,
        },
    },
)
async def transition_application(
    application_id: uuid.UUID, body: TransitionIn, actor: CurrentActor, session: Session
) -> ApplicationOut:
    """Move an Application to another Status. Saved goes to Applied or Withdrawn; Applied to
    Interview, Rejected or Withdrawn; Interview to Offer, Rejected or Withdrawn. Rejected or
    Withdrawn reopens only to the Status it was in before; Offer is final."""
    view = await service.transition(session, actor.id, application_id, body.status)
    await session.commit()
    return ApplicationOut.from_view(view)


@router.delete(
    "/applications/{application_id}",
    status_code=204,
    response_class=Response,
    operation_id="deleteApplication",
    responses={**_UNAUTHORIZED, **_NOT_FOUND},
)
async def delete_application(
    application_id: uuid.UUID, actor: CurrentActor, session: Session
) -> Response:
    """Delete an Application and its history."""
    await service.delete_application(session, actor.id, application_id)
    await session.commit()
    return Response(status_code=204)


@router.get(
    "/applications/{application_id}/history",
    response_model=ApplicationHistory,
    operation_id="getApplicationHistory",
    responses={**_UNAUTHORIZED, **_NOT_FOUND},
)
async def get_application_history(
    application_id: uuid.UUID, actor: CurrentActor, session: Session
) -> ApplicationHistory:
    """Every Status change of an Application, newest first."""
    rows = await service.history(session, actor.id, application_id)
    return ApplicationHistory(items=[StatusChange.from_row(r) for r in rows])
