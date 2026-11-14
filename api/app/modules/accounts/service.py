"""accounts.service: provisioning, admin promotion (AD-5) and consent (AD-11).

Takes the session; never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, Role
from app.core.errors import (
    ConflictError,
    ConsentRequiredError,
    ConsentVersionMismatchError,
    NotFoundError,
)
from app.core.events import ConsentRevoked, publish
from app.core.ops import record_audit
from app.modules.accounts import consent_text, repo
from app.modules.accounts.consent_text import ConsentPurpose, ConsentText
from app.modules.accounts.models import Account, Consent

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


# --------------------------------------------------------------------------- consent (AD-11)

ConsentStatusName = Literal["granted", "revoked", "none", "outdated"]


@dataclass(frozen=True)
class ConsentState:
    """What a Student sees for one purpose: the current text and their standing on it."""

    text: ConsentText
    status: ConsentStatusName
    granted_at: datetime | None
    revoked_at: datetime | None


def _state(text: ConsentText, row: Consent | None) -> ConsentState:
    if row is None:
        return ConsentState(text, "none", None, None)
    if row.revoked_at is not None:
        return ConsentState(text, "revoked", row.granted_at, row.revoked_at)
    status: ConsentStatusName = "granted" if row.version == text.version else "outdated"
    return ConsentState(text, status, row.granted_at, None)


async def list_consents(session: AsyncSession, owner_id: uuid.UUID) -> list[ConsentState]:
    """Every purpose, in declaration order, with its current text and the owner's status."""
    rows = await repo.current_consents(session, owner_id)
    return [
        _state(consent_text.current_text(purpose), rows.get(purpose.value))
        for purpose in ConsentPurpose
    ]


async def grant_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: ConsentPurpose, version: str
) -> ConsentState:
    """Agree to the CURRENT text of ``purpose``.

    ``version`` must equal the current version, else :class:`ConsentVersionMismatchError` and
    nothing is written. Idempotent when already active on that version; an active row on an
    older version is superseded (``revoked_at`` set with ``end_reason='superseded'``, no
    ``ConsentRevoked``: consent never
    lapses) and a new row is inserted.
    """
    text = consent_text.current_text(purpose)
    if version != text.version:
        raise ConsentVersionMismatchError(purpose.value, text.version)
    if not await repo.lock_for_consent_change(session, owner_id):
        raise NotFoundError("No account has this id.")
    active = await repo.get_active_consent(session, owner_id, purpose.value)
    if active is not None and active.version == text.version:
        return _state(text, active)
    if active is not None:
        await repo.end_active_consent(session, owner_id, purpose.value, "superseded")
    row = await repo.insert_consent(session, owner_id, purpose.value, text.version)
    return _state(text, row)


async def revoke_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: ConsentPurpose
) -> bool:
    """Withdraw consent for ``purpose``. Returns True when an active row was revoked.

    The revocation and ``ConsentRevoked(owner_id, purpose)`` happen in the caller's unit of
    work: subscribers run before commit and see the row already revoked; if one raises, the
    caller does not commit and the revocation rolls back. Nothing active: no write, no event.
    """
    if not await repo.lock_for_consent_change(session, owner_id):
        return False
    revoked_id = await repo.end_active_consent(session, owner_id, purpose.value, "revoked")
    if revoked_id is None:
        return False
    await publish(session, ConsentRevoked(owner_id=owner_id, purpose=purpose.value))
    return True


async def require_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: ConsentPurpose | str
) -> None:
    """THE consent gate (AD-11): the only consent check in the system.

    Passes only when ``owner_id`` has an active consent row for ``purpose`` on the purpose's
    current version. Otherwise raises :class:`ConsentRequiredError` (403
    ``/problems/consent-required`` with ``purpose``). Callers run it when they enqueue work and
    again inside the job. An unknown purpose is a programming error (``ValueError``).
    """
    text = consent_text.current_text(purpose)
    if not await repo.has_active_consent(session, owner_id, text.purpose.value, text.version):
        raise ConsentRequiredError(text.purpose.value)
