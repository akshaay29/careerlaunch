"""``/internal/*`` routes (AD-4, AD-14): secret-protected and never in the OpenAPI schema.

Every route requires ``Authorization: Bearer <secret>``, compared in constant time against
``DRAIN_SECRET`` and, during rotation, ``DRAIN_SECRET_PREVIOUS``. With no secret configured
every request is rejected (fail closed).
"""

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import DrainStaleError, UnauthorizedError
from app.core.jobs import drain

logger = logging.getLogger("app.internal")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def require_internal_secret(request: Request) -> None:
    settings = get_settings()
    candidates = [
        s.get_secret_value()
        for s in (settings.drain_secret, settings.drain_secret_previous)
        if s is not None and s.get_secret_value()
    ]
    token = _bearer_token(request)
    presented = (token or "").encode()
    # Compare against every candidate (no short-circuit) so timing does not reveal which matched.
    matched = False
    for candidate in candidates:
        matched |= hmac.compare_digest(presented, candidate.encode())
    if token is None or not matched:
        raise UnauthorizedError("A valid internal bearer secret is required.")


router = APIRouter(dependencies=[Depends(require_internal_secret)], tags=["internal"])


class DrainAccepted(BaseModel):
    status: str = "accepted"


class InternalHealth(BaseModel):
    status: str
    last_drain_at: datetime | None


async def _run_drain() -> None:
    try:
        await drain()
    except Exception:
        logger.exception("drain_failed")


@router.post("/jobs/drain", status_code=202, response_model=DrainAccepted)
async def post_drain(background: BackgroundTasks) -> DrainAccepted:
    """Accept, then drain after the response (pg_cron's pg_net call never waits on the work)."""
    background.add_task(_run_drain)
    return DrainAccepted()


@router.get("/health", response_model=InternalHealth)
async def get_internal_health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InternalHealth:
    last = (
        await session.execute(text("SELECT last_drain_at FROM drain_state WHERE id = 1"))
    ).scalar_one_or_none()
    stale_after = timedelta(minutes=get_settings().drain_stale_after_minutes)
    if last is None:
        raise DrainStaleError("No drain has completed yet.")
    if datetime.now(UTC) - last > stale_after:
        raise DrainStaleError(
            f"The last drain finished more than {get_settings().drain_stale_after_minutes} "
            "minutes ago."
        )
    return InternalHealth(status="ok", last_drain_at=last)
