"""accounts.repo: SQL for the ``accounts`` table only (AD-2)."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Account


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
