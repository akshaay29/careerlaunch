"""accounts.service: provisioning, admin promotion (AD-5), consent (AD-11), account
deletion (FR-2, AD-17), Provider Keys (FR-28, AD-6), provider status and spend caps
(AD-12).

Takes the session; never commits.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.auth import Actor, Role
from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    ConsentRequiredError,
    ConsentVersionMismatchError,
    InvalidInputError,
    NotFoundError,
    ProviderKeyUnreadableError,
    ProviderUnavailableError,
    UnauthorizedError,
)
from app.core.events import AccountDeleting, ConsentRevoked, ProviderKeyChanged, publish
from app.core.jobs import PRIORITY_QUEUE, enqueue, pause_owner_schedules
from app.core.ops import record_audit, record_event
from app.modules.accounts import COLLECTION_SCHEDULE_KINDS, consent_text, repo, tasks
from app.modules.accounts.consent_text import ConsentPurpose, ConsentText
from app.modules.accounts.models import Account, Consent, ProviderCredential
from app.ports.provider_runner import (
    ProviderAccount,
    ProviderKeyInvalid,
    ProviderRunner,
    ProviderUnavailable,
)

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


# --------------------------------------------------------------------------- provider keys (AD-6)

APIFY = "apify"
CREDENTIALS_TABLE = "provider_credentials"
INVALID_PROVIDER_KEY = (
    "Apify didn't accept this token. Check that you copied it fully and that it hasn't expired."
)


@dataclass(frozen=True)
class CredentialMeta:
    """What may leave the api about a Provider Key: never the token or the ciphertext."""

    provider: str
    has_key: bool
    last4: str | None
    validated_at: datetime | None
    # The stored key was sealed with a master-key version that is no longer configured: it
    # cannot be used and the Student must enter it again.
    needs_reentry: bool = False


def _needs_reentry(key_version: int) -> bool:
    configured = get_settings().master_keys
    # With no master key at all, the fault is the server's (503), not the Student's key.
    return bool(configured) and key_version not in configured


def _meta(provider: str, row: ProviderCredential) -> CredentialMeta:
    return CredentialMeta(
        provider, True, row.last4.strip(), row.validated_at, _needs_reentry(row.key_version)
    )


def _aad(credential_id: uuid.UUID, owner_id: uuid.UUID) -> str:
    return crypto.credential_aad(CREDENTIALS_TABLE, credential_id, owner_id)


def ensure_credentials_available() -> None:
    """Raise ``CredentialsUnavailableError`` (503) when no master key is configured, so a
    save fails before the provider is called."""
    crypto.current_key_version()


async def validate_provider_key(runner: ProviderRunner, token: SecretStr) -> ProviderAccount:
    """Check ``token`` with the provider. Takes no session: callers run it with NO database
    transaction open. A rejected token is a 422 on ``token``; an unreachable provider a 503."""
    try:
        return await runner.validate_token(token)
    except ProviderKeyInvalid:
        raise InvalidInputError(["body", "token"], INVALID_PROVIDER_KEY) from None
    except ProviderUnavailable:
        raise ProviderUnavailableError() from None


async def get_credential_meta(
    session: AsyncSession, owner_id: uuid.UUID, provider: str = APIFY
) -> CredentialMeta:
    row = await repo.get_credential(session, owner_id, provider)
    if row is None:
        return CredentialMeta(provider, False, None, None)
    return _meta(provider, row)


async def store_credential(
    session: AsyncSession,
    owner_id: uuid.UUID,
    token: SecretStr,
    provider_user_id: str | None,
    provider: str = APIFY,
) -> CredentialMeta:
    """Encrypt and store ``token`` as the owner's key for ``provider``, replacing any existing
    one in place (same row id), clear the owner's ``provider_status`` (a fixed key resumes
    collection) and publish ``ProviderKeyChanged(removed=False)``. The token
    must already be validated (outside any transaction). ``provider_runs`` consent is checked
    again under the account lock (it may have been revoked while the provider was called).
    Never commits."""
    if not await repo.lock_for_consent_change(session, owner_id):
        raise NotFoundError("No account has this id.")
    await require_consent(session, owner_id, ConsentPurpose.PROVIDER_RUNS)
    plaintext = token.get_secret_value()
    existing = await repo.get_credential(session, owner_id, provider)
    credential_id = existing.id if existing is not None else uuid.uuid7()
    blob = crypto.encrypt(plaintext, _aad(credential_id, owner_id))
    row = await repo.upsert_credential(
        session,
        credential_id=credential_id,
        owner_id=owner_id,
        provider=provider,
        ciphertext=blob,
        key_version=crypto.blob_key_version(blob),
        last4=plaintext[-4:],
        provider_user_id=provider_user_id[:100] if provider_user_id else None,
    )
    # A fixed key ends any pause (key invalid, blocked, credit): the next tick runs, and
    # caps are still checked at every start.
    await repo.delete_provider_status(session, owner_id)
    await publish(session, ProviderKeyChanged(owner_id=owner_id, removed=False))
    return _meta(provider, row)


async def delete_credential(
    session: AsyncSession, owner_id: uuid.UUID, provider: str = APIFY
) -> bool:
    """Delete the owner's key for ``provider`` and pause the owner's collection schedules
    (even with no key, so a leftover schedule never runs keyless), in the caller's unit of
    work. Publishes ``ProviderKeyChanged(removed=True)`` only when a key was deleted, and
    returns whether one was. Never commits."""
    if not await repo.lock_for_consent_change(session, owner_id):
        return False
    deleted = await repo.delete_credential(session, owner_id, provider) is not None
    await pause_owner_schedules(session, owner_id, COLLECTION_SCHEDULE_KINDS)
    if deleted:
        await publish(session, ProviderKeyChanged(owner_id=owner_id, removed=True))
    return deleted


async def on_consent_revoked(session: AsyncSession, event: ConsentRevoked) -> None:
    """``ConsentRevoked`` subscriber (AD-11): withdrawing ``provider_runs`` pauses the owner's
    collection schedules in the revoking transaction. The Provider Key is kept."""
    if event.purpose == ConsentPurpose.PROVIDER_RUNS.value:
        await pause_owner_schedules(session, event.owner_id, COLLECTION_SCHEDULE_KINDS)


async def decrypt_credential(
    session: AsyncSession, owner_id: uuid.UUID, provider: str = APIFY
) -> SecretStr | None:
    """The owner's token for ``provider`` (None without a key), for the callers of adapters
    only (Story 2.3's ``start_provider_run``). Never log, return or store the result.
    Raises ``CredentialsUnavailableError`` (503) without a master key and
    ``ProviderKeyUnreadableError`` (409: re-enter the key) when the stored blob does not
    decrypt."""
    row = await repo.get_credential(session, owner_id, provider)
    if row is None:
        return None
    try:
        return SecretStr(crypto.decrypt(row.ciphertext, _aad(row.id, owner_id)))
    except crypto.CredentialError:
        raise ProviderKeyUnreadableError() from None


# --------------------------------------------------------------------------- provider status


PauseReason = Literal["key_invalid", "credit_exhausted", "not_permitted", "account_blocked"]
PAUSE_REASONS: tuple[PauseReason, ...] = (
    "key_invalid",
    "credit_exhausted",
    "not_permitted",
    "account_blocked",
)
CREDIT_EXHAUSTED: PauseReason = "credit_exhausted"


@dataclass(frozen=True)
class ProviderStatusView:
    """The owner's collection status (AD-12). ``status`` is ``active`` or ``paused`` (with a
    ``reason``); ``exhausted_until`` (credit exhausted) blocks runs until that time, after
    which a ``credit_exhausted`` pause no longer blocks (collection resumes on its own)."""

    status: Literal["active", "paused"]
    reason: str | None
    exhausted_until: datetime | None

    def blocks_runs(self, now: datetime) -> str | None:
        """Why a run may not start now (``paused`` or ``exhausted``), or None."""
        exhausted = self.exhausted_until is not None and self.exhausted_until > now
        if self.status != "active":
            if self.reason == CREDIT_EXHAUSTED and self.exhausted_until is not None:
                return "exhausted" if exhausted else None
            return "paused"
        return "exhausted" if exhausted else None

    def effective(self, now: datetime) -> ProviderStatusView:
        """The status as the Student sees it ``now``: a pause that no longer blocks reads as
        active."""
        if self.blocks_runs(now) is None:
            return ACTIVE_PROVIDER_STATUS
        if self.status == "active":  # active with credit exhausted until a future time
            return ProviderStatusView("paused", CREDIT_EXHAUSTED, self.exhausted_until)
        return self


ACTIVE_PROVIDER_STATUS = ProviderStatusView("active", None, None)


async def get_provider_status(session: AsyncSession, owner_id: uuid.UUID) -> ProviderStatusView:
    """The owner's provider status as stored; no row means active."""
    row = await repo.get_provider_status(session, owner_id)
    if row is None:
        return ACTIVE_PROVIDER_STATUS
    status: Literal["active", "paused"] = "active" if row.status == "active" else "paused"
    return ProviderStatusView(status, row.reason, row.exhausted_until)


async def credential_replaced_since(
    session: AsyncSession, owner_id: uuid.UUID, since: datetime, provider: str = APIFY
) -> bool:
    """True when the owner's key for ``provider`` was saved after ``since``. A failure of an
    older key must not pause a Student who has just saved a new one."""
    row = await repo.get_credential(session, owner_id, provider)
    return row is not None and row.updated_at > since


async def pause_provider(
    session: AsyncSession,
    owner_id: uuid.UUID,
    reason: PauseReason,
    exhausted_until: datetime | None = None,
) -> None:
    """Pause the owner's collection with ``reason`` in the caller's transaction (called by
    ``listings`` when Apify refuses a Student run). ``exhausted_until`` (credit exhausted
    only) is when the pause stops blocking. Schedules are never touched: while paused every
    start is refused cheaply. ``store_credential`` clears the pause."""
    if reason not in PAUSE_REASONS:
        raise ValueError(f"unknown pause reason {reason!r}")
    if (exhausted_until is not None) != (reason == CREDIT_EXHAUSTED):
        raise ValueError("exhausted_until is set for a credit_exhausted pause only")
    await repo.upsert_provider_status(
        session, owner_id, status="paused", reason=reason, exhausted_until=exhausted_until
    )


# --------------------------------------------------------------------------- spend caps

PER_RUN_MIN = Decimal("0.05")
PER_RUN_MAX = Decimal("2.00")
MONTHLY_MIN = Decimal("0.25")
MONTHLY_MAX = Decimal(100)
CAP_ORDER_MESSAGE = "The monthly cap must be at least the per-run cap."


@dataclass(frozen=True)
class SpendCapsView:
    """The owner's caps in USD; ``is_default`` when the Student never set their own."""

    per_run_usd: Decimal
    monthly_usd: Decimal
    is_default: bool


def default_spend_caps() -> SpendCapsView:
    settings = get_settings()
    return SpendCapsView(
        Decimal(str(settings.collect_max_charge_usd)),
        Decimal(str(settings.default_monthly_cap_usd)),
        True,
    )


async def get_spend_caps(session: AsyncSession, owner_id: uuid.UUID) -> SpendCapsView:
    """The owner's caps; no row means the defaults."""
    row = await repo.get_spend_caps(session, owner_id)
    if row is None:
        return default_spend_caps()
    return SpendCapsView(row.per_run_usd, row.monthly_usd, False)


async def set_spend_caps(
    session: AsyncSession, owner_id: uuid.UUID, per_run_usd: Decimal, monthly_usd: Decimal
) -> SpendCapsView:
    """Store the owner's caps (USD, at most 4 decimals). Per-run 0.05..2.00, monthly
    0.25..100 and monthly >= per-run; anything else is a 422 and nothing is written."""
    per_run = Decimal(per_run_usd).quantize(Decimal("0.0001"))
    monthly = Decimal(monthly_usd).quantize(Decimal("0.0001"))
    if not PER_RUN_MIN <= per_run <= PER_RUN_MAX:
        raise InvalidInputError(
            ["body", "per_run_usd"], "The per-run cap must be between USD 0.05 and 2.00."
        )
    if not MONTHLY_MIN <= monthly <= MONTHLY_MAX:
        raise InvalidInputError(
            ["body", "monthly_usd"], "The monthly cap must be between USD 0.25 and 100."
        )
    if monthly < per_run:
        raise InvalidInputError(["body", "monthly_usd"], CAP_ORDER_MESSAGE)
    if not await repo.lock_for_consent_change(session, owner_id):
        raise NotFoundError("No account has this id.")
    await repo.upsert_spend_caps(session, owner_id, per_run_usd=per_run, monthly_usd=monthly)
    return SpendCapsView(per_run, monthly, False)
