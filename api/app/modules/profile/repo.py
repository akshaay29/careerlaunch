"""profile.repo: SQL for the ``resumes`` table only (AD-2). Every query is owner-scoped."""

import uuid

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.profile.models import Resume


async def lock_owner_resumes(session: AsyncSession, owner_id: uuid.UUID) -> None:
    """Serialise Resume changes of one owner (cap check, activation) until the transaction
    ends. A transaction-scoped advisory lock, so no other module's table is touched."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"resumes:{owner_id}"},
    )


async def count(session: AsyncSession, owner_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Resume).where(Resume.owner_id == owner_id)
    )
    return int(result.scalar_one())


async def has_active(session: AsyncSession, owner_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(Resume.id).where(Resume.owner_id == owner_id, Resume.is_active).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def insert_resume(
    session: AsyncSession,
    *,
    resume_id: uuid.UUID,
    owner_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    is_active: bool,
) -> Resume:
    stmt = (
        insert(Resume)
        .values(
            id=resume_id,
            owner_id=owner_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            is_active=is_active,
        )
        .returning(Resume)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def get(session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> Resume | None:
    result = await session.execute(
        select(Resume)
        .where(Resume.owner_id == owner_id, Resume.id == resume_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_active(session: AsyncSession, owner_id: uuid.UUID) -> Resume | None:
    result = await session.execute(
        select(Resume)
        .where(Resume.owner_id == owner_id, Resume.is_active)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_page(
    session: AsyncSession, owner_id: uuid.UUID, *, limit: int, before: uuid.UUID | None
) -> list[Resume]:
    """Newest first (UUID v7 ids are time-ordered); ``before`` is the cursor (exclusive)."""
    stmt = select(Resume).where(Resume.owner_id == owner_id)
    if before is not None:
        stmt = stmt.where(Resume.id < before)
    stmt = stmt.order_by(Resume.id.desc()).limit(limit).execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars())


async def set_active(session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    """Deactivate the owner's active row, then activate ``resume_id`` (two statements, in
    this order, so the one-active index holds at every step)."""
    await session.execute(
        update(Resume)
        .where(Resume.owner_id == owner_id, Resume.is_active, Resume.id != resume_id)
        .values(is_active=False)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Resume)
        .where(Resume.owner_id == owner_id, Resume.id == resume_id)
        .values(is_active=True)
        .execution_options(synchronize_session=False)
    )


async def delete_resume(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume | None:
    """Delete the row; returns it (or None when the owner has no such row)."""
    result = await session.execute(
        delete(Resume)
        .where(Resume.owner_id == owner_id, Resume.id == resume_id)
        .returning(Resume)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none()
