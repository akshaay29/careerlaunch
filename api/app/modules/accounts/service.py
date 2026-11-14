"""accounts.service: provisioning, admin promotion (AD-5), consent (AD-11) and account
deletion (FR-2, AD-17).

Takes the session; never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Actor, Role
from app.core.errors import (
    ConflictError,
    ConsentRequiredError,
    ConsentVersionMismatchError,
    InvalidInputError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.events import AccountDeleting, ConsentRevoked, publish
from app.core.jobs import PRIORITY_QUEUE, enqueue
from app.core.ops import record_audit, record_event
from app.modules.accounts import consent_text, repo, tasks
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
            # Conditional on "no tombstone" in the same statement, so a deletion committing
            # concurrently cannot be undone by re-provisioning.
            account = await repo.upsert_email(session, account_id, email)
    except IntegrityError as exc:
        if _constraint_name(exc) != EMAIL_UNIQUE_CONSTRAINT:
            raise
        # The email belongs to another accounts row (e.g. an Auth user deleted outside the app
        # and re-created with the same address).
        raise ConflictError("This email is already linked to another account.") from exc
    if account is None:
        # A deleted account's tokens stay valid until they expire; never re-provision it.
        raise UnauthorizedError("This account has been deleted.")
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
    if not await has_consent(session, owner_id, purpose):
        raise ConsentRequiredError(consent_text.current_text(purpose).purpose.value)


async def has_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: ConsentPurpose | str
) -> bool:
    """The same rule as :func:`require_consent`, without raising: True only when
    ``owner_id`` has an active consent on the purpose's current version. For callers whose
    behaviour (not permission) depends on consent, e.g. pool contribution."""
    text = consent_text.current_text(purpose)
    return await repo.has_active_consent(session, owner_id, text.purpose.value, text.version)


# --------------------------------------------------------------------------- deletion (AD-17)

ACCOUNT_DELETED_EVENT = "account.deleted"
CONFIRM_EMAIL_MISMATCH = "Type the email address of your account to confirm."


async def delete_account(session: AsyncSession, owner_id: uuid.UUID, confirm_email: str) -> bool:
    """Delete the account ``owner_id`` and everything it owns, in the caller's unit of work.

    ``confirm_email`` must equal the account email after trimming and ignoring case, else
    :class:`InvalidInputError` on ``confirm_email`` and nothing is written. Then, in order:
    publish ``AccountDeleting`` (subscribers still see the owner's rows), insert the
    tombstone, record ``account.deleted`` (no owner id), enqueue the idempotent
    ``purge_files`` and ``delete_auth_user`` jobs, and delete the ``accounts`` row (which
    cascades every Student-owned row and sets global rows' references to NULL).

    Returns True when this call deleted the account, False when a concurrent deletion had
    already done it (tombstone present; nothing written, no new jobs).
    """
    account = await repo.get_for_update(session, owner_id)
    if account is None:
        if await repo.is_deleted(session, owner_id):
            return False
        raise NotFoundError("No account has this id.")
    if normalize_email(confirm_email) != normalize_email(account.email):
        raise InvalidInputError(["body", "confirm_email"], CONFIRM_EMAIL_MISMATCH)

    await publish(session, AccountDeleting(owner_id=owner_id))
    await repo.insert_tombstone(session, owner_id)
    await record_event(session, ACCOUNT_DELETED_EVENT, owner_id=None, counts={})
    await _enqueue_purge(session, tasks.PURGE_FILES, owner_id)
    await _enqueue_purge(session, tasks.DELETE_AUTH_USER, owner_id)
    await repo.delete_account(session, owner_id)
    return True


async def _enqueue_purge(session: AsyncSession, task_name: str, owner_id: uuid.UUID) -> None:
    key = f"{task_name}:account:{owner_id}"
    await enqueue(session, task_name, key, queue=PRIORITY_QUEUE, owner_id=owner_id)


# A deletion younger than this is left to its own jobs and their retries.
RESUME_AFTER = timedelta(hours=1)
RESUME_BATCH = 500


async def resume_pending_deletions(session: AsyncSession) -> int:
    """Re-enqueue the missing ``purge_files`` / ``delete_auth_user`` job(s) of every tombstone
    older than :data:`RESUME_AFTER` that is not finished. Returns the number of jobs enqueued.
    The keys are the original ones, so the queueing lock drops a duplicate of a pending run."""
    enqueued = 0
    for tombstone in await repo.pending_deletions(session, RESUME_AFTER, RESUME_BATCH):
        if tombstone.files_purged_at is None:
            await _enqueue_purge(session, tasks.PURGE_FILES, tombstone.id)
            enqueued += 1
        if tombstone.auth_deleted_at is None:
            await _enqueue_purge(session, tasks.DELETE_AUTH_USER, tombstone.id)
            enqueued += 1
    return enqueued
