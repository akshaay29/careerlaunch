"""accounts.service: provisioning and admin promotion (AD-5). Takes the session; never commits."""

import uuid
from typing import cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, Role
from app.core.errors import ConflictError, NotFoundError
from app.core.ops import record_audit
from app.modules.accounts import repo
from app.modules.accounts.models import Account

PROMOTE_ADMIN_ACTION = "account.promote_admin"
EMAIL_UNIQUE_CONSTRAINT = "accounts_email_key"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _actor(account: Account) -> Actor:
    return Actor(id=account.id, email=account.email, role=cast(Role, account.role))


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


async def get_or_provision(
    session: AsyncSession, account_id: uuid.UUID, email: str
) -> tuple[Actor, bool]:
    """Return ``(actor, wrote)`` for a verified token, creating ``accounts`` on first sight.

    New rows get role ``student``. An existing row keeps its role; its email is refreshed if
    the token's (lower-cased) email changed. ``wrote`` is False on the read-only path (row
    exists, email unchanged), so the caller has nothing to commit.
    """
    email = normalize_email(email)
    account = await repo.get(session, account_id)
    if account is not None and account.email == email:
        return _actor(account), False
    try:
        async with session.begin_nested():
            account = await repo.upsert_email(session, account_id, email)
    except IntegrityError as exc:
        if _constraint_name(exc) != EMAIL_UNIQUE_CONSTRAINT:
            raise
        # The email belongs to another accounts row (e.g. an Auth user deleted outside the app
        # and re-created with the same address).
        raise ConflictError("This email is already linked to another account.") from exc
    return _actor(account), True


async def promote_admin(session: AsyncSession, email: str) -> tuple[Actor, bool]:
    """Set the role of the account with ``email`` to ``admin`` and audit it (system actor).

    Returns ``(actor, changed)``. Raises :class:`NotFoundError` (nothing written) when no
    account has that email. Promoting an existing admin writes nothing (``changed`` False).
    """
    account = await repo.get_by_email_for_update(session, normalize_email(email))
    if account is None:
        raise NotFoundError("No account has this email.")
    if account.role == "admin":
        return _actor(account), False
    await repo.set_role(session, account.id, "admin")
    await record_audit(
        session, PROMOTE_ADMIN_ACTION, actor_id=None, target_account_id=account.id
    )
    account = await repo.get(session, account.id)
    assert account is not None
    return _actor(account), True
