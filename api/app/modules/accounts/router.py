"""accounts routers.

``router`` is mounted under ``/api/v1`` (valid token required) and ``admin_router`` under
``/api/v1/admin`` (admin role required) by ``app.main``; the guards are router-level there.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, current_actor, require_admin
from app.core.db import get_session
from app.core.jobs import drain_priority_after_response
from app.modules.accounts import consent_text, service
from app.modules.accounts.consent_text import ConsentPurpose
from app.modules.accounts.schemas import (
    AccountDeletion,
    ConsentGrant,
    ConsentOverview,
    ConsentStatus,
    Me,
    ProviderKeyInput,
    ProviderKeyMeta,
    RightsNotice,
    SpendCapsInput,
    SpendCapsOut,
)
from app.ports.provider_runner import ProviderRunner, provider_runner_dependency

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


# --------------------------------------------------------------------------- deletion (AD-17)

# The shared after-response priority drain; a module global so tests can replace it.
drain_priority = drain_priority_after_response


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="deleteMe",
    responses={
        422: {
            "description": "confirm_email does not match the account email",
            "content": {"application/problem+json": {}},
        }
    },
)
async def delete_me(
    body: AccountDeletion,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background: BackgroundTasks,
) -> Response:
    """Delete my account and all my data (FR-2). Confirmed by typing the account email.
    Stored files and the Auth user are removed by jobs, started right after the response."""
    if await service.delete_account(session, actor.id, body.confirm_email):
        await session.commit()
        background.add_task(drain_priority)
    # else: a concurrent request already deleted it; nothing new to commit or run.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


# --------------------------------------------------------------------------- provider key (AD-6)

Runner = Annotated[ProviderRunner, Depends(provider_runner_dependency)]


def _key_meta(meta: service.CredentialMeta) -> ProviderKeyMeta:
    return ProviderKeyMeta.model_validate(
        {
            "provider": meta.provider,
            "has_key": meta.has_key,
            "last4": meta.last4,
            "validated_at": meta.validated_at,
            "needs_reentry": meta.needs_reentry,
        }
    )


@router.get("/provider-key", response_model=ProviderKeyMeta, operation_id="getProviderKey")
async def get_provider_key(actor: CurrentActor, session: Session) -> ProviderKeyMeta:
    return _key_meta(await service.get_credential_meta(session, actor.id))


@router.put(
    "/provider-key",
    response_model=ProviderKeyMeta,
    operation_id="putProviderKey",
    responses={
        403: {"description": "provider_runs consent is required", **_PROBLEM},
        422: {"description": "Apify did not accept the token", **_PROBLEM},
        503: {"description": "Apify or credential storage is unavailable", **_PROBLEM},
    },
)
async def put_provider_key(
    body: ProviderKeyInput, actor: CurrentActor, session: Session, runner: Runner
) -> ProviderKeyMeta:
    """Validate the Apify token with Apify, then store it encrypted (replacing any existing
    key). The token is never returned. Apify is called with no database transaction open."""
    await service.require_consent(session, actor.id, ConsentPurpose.PROVIDER_RUNS)
    service.ensure_credentials_available()
    # End the read transaction: no connection is held while Apify answers.
    await session.rollback()
    account = await service.validate_provider_key(runner, body.token)
    # Short store transaction; store_credential re-checks consent under the account lock.
    meta = await service.store_credential(session, actor.id, body.token, account.user_id)
    await session.commit()
    return _key_meta(meta)


@router.delete(
    "/provider-key",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="deleteProviderKey",
)
async def delete_provider_key(actor: CurrentActor, session: Session) -> Response:
    """Delete my Provider Key and pause my collection. Idempotent."""
    await service.delete_credential(session, actor.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- spend caps (AD-12)


def _caps_out(caps: service.SpendCapsView) -> SpendCapsOut:
    return SpendCapsOut(
        per_run_usd=float(caps.per_run_usd),
        monthly_usd=float(caps.monthly_usd),
        is_default=caps.is_default,
    )


@router.get("/spend-caps", response_model=SpendCapsOut, operation_id="getSpendCaps")
async def get_spend_caps(actor: CurrentActor, session: Session) -> SpendCapsOut:
    return _caps_out(await service.get_spend_caps(session, actor.id))


@router.put(
    "/spend-caps",
    response_model=SpendCapsOut,
    operation_id="putSpendCaps",
    responses={422: {"description": "A cap is out of bounds", **_PROBLEM}},
)
async def put_spend_caps(
    body: SpendCapsInput, actor: CurrentActor, session: Session
) -> SpendCapsOut:
    """Set my per-run and monthly caps. They apply from the next run."""
    caps = await service.set_spend_caps(session, actor.id, body.per_run_usd, body.monthly_usd)
    await session.commit()
    return _caps_out(caps)
