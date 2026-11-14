"""accounts.repo: SQL for the ``accounts``, ``consents`` and ``deleted_accounts`` tables only
(AD-2)."""

import uuid
from datetime import timedelta
from typing import Literal

from sqlalchemy import Text, Uuid, delete, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Account, Consent, DeletedAccount


async def get(session: AsyncSession, account_id: uuid.UUID) -> Account | None:
    return await session.get(Account, account_id, populate_existing=True)


async def get_by_email(session: AsyncSession, email: str) -> Account | None:
    result = await session.execute(select(Account).where(Account.email == email))
    return result.scalar_one_or_none()


async def get_by_email_for_update(session: AsyncSession, email: str) -> Account | None:
    result = await session.execute(
        select(Account).where(Account.email == email).with_for_update()
    )
    return result.scalar_one_or_none()


async def upsert_email(
    session: AsyncSession, account_id: uuid.UUID, email: str
) -> Account | None:
    """Insert ``(id, email)`` with the default role, or refresh the email of an existing row.
    The role is never written here.

    The insert is conditional, in the same statement, on ``account_id`` having no tombstone in
    ``deleted_accounts``; returns None when nothing was written because of a tombstone.
    """
    tombstoned = exists().where(DeletedAccount.id == account_id)
    source = select(
        literal(account_id, type_=Uuid()).label("id"), literal(email, type_=Text()).label("email")
    ).where(~tombstoned)
    stmt = (
        insert(Account)
        .from_select(["id", "email"], source)
        .on_conflict_do_update(index_elements=[Account.id], set_={"email": email})
        .returning(Account)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_role(session: AsyncSession, account_id: uuid.UUID, role: str) -> None:
    await session.execute(update(Account).where(Account.id == account_id).values(role=role))


async def lock_for_consent_change(session: AsyncSession, account_id: uuid.UUID) -> bool:
    """Lock the account row (``FOR NO KEY UPDATE``) so consent changes of one owner are
    serialised. Returns False when the account does not exist."""
    result = await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update(key_share=True)
    )
    return result.scalar_one_or_none() is not None


async def get_for_update(session: AsyncSession, account_id: uuid.UUID) -> Account | None:
    result = await session.execute(
        select(Account)
        .where(Account.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def delete_account(session: AsyncSession, account_id: uuid.UUID) -> bool:
    """Delete the ``accounts`` row; every Student-owned row goes with it (ON DELETE CASCADE)
    and global rows' Student references become NULL (ON DELETE SET NULL)."""
    result = await session.execute(
        delete(Account)
        .where(Account.id == account_id)
        .returning(Account.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


# --------------------------------------------------------------------------- tombstones


async def is_deleted(session: AsyncSession, account_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(DeletedAccount.id).where(DeletedAccount.id == account_id)
    )
    return result.scalar_one_or_none() is not None


async def mark_files_purged(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Record that the account's stored files are gone (first success wins; no-op without a
    tombstone)."""
    await session.execute(
        update(DeletedAccount)
        .where(DeletedAccount.id == account_id, DeletedAccount.files_purged_at.is_(None))
        .values(files_purged_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def mark_auth_deleted(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Record that the account's Auth user is gone (first success wins; no-op without a
    tombstone)."""
    await session.execute(
        update(DeletedAccount)
        .where(DeletedAccount.id == account_id, DeletedAccount.auth_deleted_at.is_(None))
        .values(auth_deleted_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def pending_deletions(
    session: AsyncSession, older_than: timedelta, limit: int
) -> list[DeletedAccount]:
    """Tombstones older than ``older_than`` whose files or Auth user are not yet removed."""
    result = await session.execute(
        select(DeletedAccount)
        .where(
            DeletedAccount.deleted_at < func.now() - older_than,
            or_(
                DeletedAccount.files_purged_at.is_(None),
                DeletedAccount.auth_deleted_at.is_(None),
            ),
        )
        .order_by(DeletedAccount.deleted_at)
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def insert_tombstone(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Record ``account_id`` as deleted (idempotent)."""
    await session.execute(
        insert(DeletedAccount).values(id=account_id).on_conflict_do_nothing(index_elements=["id"])
    )


# --------------------------------------------------------------------------- consents


async def get_active_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: str
) -> Consent | None:
    result = await session.execute(
        select(Consent)
        .where(
            Consent.owner_id == owner_id,
            Consent.purpose == purpose,
            Consent.revoked_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def current_consents(session: AsyncSession, owner_id: uuid.UUID) -> dict[str, Consent]:
    """Per purpose: the active row if there is one, else the most recent row."""
    result = await session.execute(
        select(Consent)
        .where(Consent.owner_id == owner_id)
        .ext(distinct_on(Consent.purpose))
        .order_by(
            Consent.purpose,
            Consent.revoked_at.is_(None).desc(),
            Consent.granted_at.desc(),
            Consent.id.desc(),
        )
        .execution_options(populate_existing=True)
    )
    return {row.purpose: row for row in result.scalars()}


async def insert_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: str, version: str
) -> Consent:
    stmt = (
        insert(Consent)
        .values(id=uuid.uuid7(), owner_id=owner_id, purpose=purpose, version=version)
        .returning(Consent)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def end_active_consent(
    session: AsyncSession,
    owner_id: uuid.UUID,
    purpose: str,
    reason: Literal["revoked", "superseded"],
) -> uuid.UUID | None:
    """End the active row: ``revoked_at = now()`` and ``end_reason = reason``. Returns its id,
    or None if none was active."""
    result = await session.execute(
        update(Consent)
        .where(
            Consent.owner_id == owner_id,
            Consent.purpose == purpose,
            Consent.revoked_at.is_(None),
        )
        .values(revoked_at=func.now(), end_reason=reason)
        .returning(Consent.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none()


async def has_active_consent(
    session: AsyncSession, owner_id: uuid.UUID, purpose: str, version: str
) -> bool:
    result = await session.execute(
        select(Consent.id)
        .where(
            Consent.owner_id == owner_id,
            Consent.purpose == purpose,
            Consent.version == version,
            Consent.revoked_at.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
