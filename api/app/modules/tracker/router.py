"""tracker router (mounted under ``/api/v1``; a valid token is required by ``app.main``).

Saving a Listing as an Application and tracking its Status (FR-16, Story 4.1). Another
Student's Application is always a 404. Routers commit; the service never does.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor
from app.core.db import get_session
from app.modules.tracker import service
from app.modules.tracker.schemas import (
    ApplicationForListing,
    ApplicationHistory,
    ApplicationOut,
    ApplicationUpdate,
    SaveListingIn,
    StatusChange,
    TransitionIn,
)

router = APIRouter(tags=["tracker"])

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}
_UNAUTHORIZED = {401: {"description": "Not signed in", **_PROBLEM}}
_NOT_FOUND = {404: {"description": "No such Application for this Student", **_PROBLEM}}


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
