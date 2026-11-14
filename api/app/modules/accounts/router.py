"""accounts routers.

``router`` is mounted under ``/api/v1`` (valid token required) and ``admin_router`` under
``/api/v1/admin`` (admin role required) by ``app.main``; the guards are router-level there.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor, require_admin
from app.core.db import get_session
from app.modules.accounts import consent_text, service
from app.modules.accounts.consent_text import ConsentPurpose
from app.modules.accounts.schemas import (
    ConsentGrant,
    ConsentOverview,
    ConsentStatus,
    Me,
    RightsNotice,
)

router = APIRouter(tags=["accounts"])
admin_router = APIRouter(tags=["admin"])


def _me(actor: Actor) -> Me:
    return Me(id=actor.id, email=actor.email, role=actor.role)


@router.get("/me", response_model=Me, operation_id="getMe")
async def get_me(actor: Annotated[Actor, Depends(current_actor)]) -> Me:
    return _me(actor)


@admin_router.get("/me", response_model=Me, operation_id="getAdminMe")
async def get_admin_me(actor: Annotated[Actor, Depends(require_admin)]) -> Me:
    return _me(actor)


# --------------------------------------------------------------------------- consent (AD-11)

Session = Annotated[AsyncSession, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(current_actor)]

_PROBLEM = {"content": {"application/problem+json": {}}}


def _consent_status(state: service.ConsentState) -> ConsentStatus:
    return ConsentStatus(
        purpose=state.text.purpose,
        title=state.text.title,
        body=list(state.text.body),
        version=state.text.version,
        status=state.status,
        granted_at=state.granted_at,
        revoked_at=state.revoked_at,
    )


@router.get("/consents", response_model=ConsentOverview, operation_id="listConsents")
async def list_consents(actor: CurrentActor, session: Session) -> ConsentOverview:
    states = await service.list_consents(session, actor.id)
    notice = consent_text.RIGHTS_NOTICE
    return ConsentOverview(
        purposes=[_consent_status(s) for s in states],
        rights=RightsNotice(title=notice.title, body=list(notice.body)),
        notice_version=notice.version,
    )


@router.put(
    "/consents/{purpose}",
    response_model=ConsentStatus,
    operation_id="grantConsent",
    responses={409: {"description": "The consent text version changed", **_PROBLEM}},
)
async def grant_consent(
    purpose: ConsentPurpose, body: ConsentGrant, actor: CurrentActor, session: Session
) -> ConsentStatus:
    state = await service.grant_consent(session, actor.id, purpose, body.version)
    await session.commit()
    return _consent_status(state)


@router.delete(
    "/consents/{purpose}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="revokeConsent",
)
async def revoke_consent(purpose: ConsentPurpose, actor: CurrentActor, session: Session) -> Response:
    if await service.revoke_consent(session, actor.id, purpose):
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
