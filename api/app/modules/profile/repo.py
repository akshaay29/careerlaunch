"""profile.repo: SQL for the ``resumes``, ``skill_profiles`` and ``preferences`` tables only
(AD-2). Every query is owner-scoped."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.profile.models import Preferences, Resume, SkillProfile


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


# --------------------------------------------------------------------------- skill_profiles


async def lock_owner_skill_profile(session: AsyncSession, owner_id: uuid.UUID) -> None:
    """Serialise Skill Profile edits of one owner until the transaction ends (the row may not
    exist yet, so a row lock is not enough)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"skill_profiles:{owner_id}"},
    )


async def get_skill_profile(session: AsyncSession, owner_id: uuid.UUID) -> SkillProfile | None:
    result = await session.execute(
        select(SkillProfile)
        .where(SkillProfile.owner_id == owner_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def mark_skill_profile_pending(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> None:
    """Create the row (or point the existing one at ``resume_id``) with status ``pending``.
    The extracted set and the Student's edits are kept until the new extraction lands."""
    stmt = insert(SkillProfile).values(owner_id=owner_id, resume_id=resume_id, status="pending")
    stmt = stmt.on_conflict_do_update(
        index_elements=[SkillProfile.owner_id],
        set_={"resume_id": resume_id, "status": "pending", "updated_at": func.now()},
    )
    await session.execute(stmt)


async def settle_pending_skill_profile(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> None:
    """The source Resume of a PENDING profile is being deleted: its extraction will never
    land, so drop back to the no-extraction state (status NULL). Edits are kept."""
    await session.execute(
        update(SkillProfile)
        .where(
            SkillProfile.owner_id == owner_id,
            SkillProfile.resume_id == resume_id,
            SkillProfile.status == "pending",
        )
        .values(status=None, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def store_extraction(
    session: AsyncSession,
    owner_id: uuid.UUID,
    resume_id: uuid.UUID,
    *,
    status: str,
    extracted_skills: list[str],
    vocab_version: str,
    education: list[Any],
    experience: list[Any],
    extracted_at: datetime,
) -> bool:
    """Store an extraction result, only while the row still points at ``resume_id``.
    Returns False (nothing written) when it no longer does."""
    result = await session.execute(
        update(SkillProfile)
        .where(SkillProfile.owner_id == owner_id, SkillProfile.resume_id == resume_id)
        .values(
            status=status,
            extracted_skills=extracted_skills,
            vocab_version=vocab_version,
            education=education,
            experience=experience,
            extracted_at=extracted_at,
            updated_at=func.now(),
        )
        .returning(SkillProfile.owner_id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def save_skill_edits(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    added: list[str],
    removed: list[str],
    custom: list[str],
) -> None:
    """Write the Student's edit sets, creating the row (no status) when there is none."""
    values = {"added_skills": added, "removed_skills": removed, "custom_skills": custom}
    stmt = insert(SkillProfile).values(owner_id=owner_id, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SkillProfile.owner_id], set_={**values, "updated_at": func.now()}
    )
    await session.execute(stmt)


# --------------------------------------------------------------------------- preferences


async def lock_owner_preferences(session: AsyncSession, owner_id: uuid.UUID) -> None:
    """Serialise Preferences saves of one owner until the transaction ends (the row may not
    exist yet, so a row lock is not enough)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"preferences:{owner_id}"},
    )


async def get_preferences(session: AsyncSession, owner_id: uuid.UUID) -> Preferences | None:
    result = await session.execute(
        select(Preferences)
        .where(Preferences.owner_id == owner_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def upsert_preferences(
    session: AsyncSession, owner_id: uuid.UUID, values: dict[str, Any]
) -> Preferences:
    """Replace the owner's whole Preferences row (insert it when there is none)."""
    stmt = insert(Preferences).values(owner_id=owner_id, **values)
    stmt = (
        stmt.on_conflict_do_update(
            index_elements=[Preferences.owner_id], set_={**values, "updated_at": func.now()}
        )
        .returning(Preferences)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()
