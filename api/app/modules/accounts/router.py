"""accounts routers.

``router`` is mounted under ``/api/v1`` (valid token required) and ``admin_router`` under
``/api/v1/admin`` (admin role required) by ``app.main``; the guards are router-level there.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import Actor, current_actor, require_admin
from app.modules.accounts.schemas import Me

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
