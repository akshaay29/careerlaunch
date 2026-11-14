"""accounts.repo: SQL for the ``accounts`` and ``consents`` tables only (AD-2)."""

import uuid
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Account, Consent


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


async def upsert_email(session: AsyncSession, account_id: uuid.UUID, email: str) -> Account:
    """Insert ``(id, email)`` with the default role, or refresh the email of an existing row.
    The role is never written here."""
    stmt = (
        insert(Account)
        .values(id=account_id, email=email)
        .on_conflict_do_update(index_elements=[Account.id], set_={"email": email})
        .returning(Account)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def set_role(session: AsyncSession, account_id: uuid.UUID, role: str) -> None:
    await session.execute(update(Account).where(Account.id == account_id).values(role=role))


async def lock_for_consent_change(session: AsyncSession, account_id: uuid.UUID) -> bool:
    """Lock the account row (``FOR NO KEY UPDATE``) so consent changes of one owner are
    serialised. Returns False when the account does not exist."""
    result = await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update(key_share=True)
    )
    return result.scalar_one_or_none() is not None


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
