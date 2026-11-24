"""tracker.repo: SQL for ``applications`` and ``application_status_history`` only (AD-2).
Every function takes ``owner_id`` from the auth context (AD-5)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tracker.models import Application, ApplicationStatusHistory


async def insert_application(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    listing_id: uuid.UUID,
    status: str,
    title: str,
    company: str,
    url: str,
    deadline: date | None,
    now: datetime,
) -> Application | None:
    """Insert one Application, or nothing when the owner already saved ``listing_id``
    (partial unique key). Returns the new row, or None on that conflict."""
    stmt = (
        insert(Application)
        .values(
            id=uuid.uuid7(),
            owner_id=owner_id,
            listing_id=listing_id,
            status=status,
            title=title,
            company=company,
            url=url,
            deadline=deadline,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["owner_id", "listing_id"],
            index_where=Application.listing_id.is_not(None),
        )
        .returning(Application)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get(
    session: AsyncSession,
    owner_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Application | None:
    stmt = (
        select(Application)
        .where(Application.owner_id == owner_id, Application.id == application_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> Application | None:
    stmt = (
        select(Application)
        .where(Application.owner_id == owner_id, Application.listing_id == listing_id)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def listing_ids(session: AsyncSession, owner_id: uuid.UUID) -> list[uuid.UUID]:
    """The Listing ids the owner's Applications still reference (one per Application)."""
    stmt = select(Application.listing_id).where(
        Application.owner_id == owner_id, Application.listing_id.is_not(None)
    )
    return [r for r in (await session.execute(stmt)).scalars() if r is not None]


async def update_application(
    session: AsyncSession,
    owner_id: uuid.UUID,
    application_id: uuid.UUID,
    **values: Any,
) -> Application:
    stmt = (
        update(Application)
        .where(Application.owner_id == owner_id, Application.id == application_id)
        .values(**values)
        .returning(Application)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def insert_history(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    owner_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    changed_at: datetime,
) -> None:
    session.add(
        ApplicationStatusHistory(
            id=uuid.uuid7(),
            application_id=application_id,
            owner_id=owner_id,
            from_status=from_status,
            to_status=to_status,
            changed_at=changed_at,
        )
    )
    await session.flush()


async def status_before_closing(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID, closed_status: str
) -> str | None:
    """``from_status`` of the latest history row that entered ``closed_status``."""
    stmt = (
        select(ApplicationStatusHistory.from_status)
        .where(
            ApplicationStatusHistory.owner_id == owner_id,
            ApplicationStatusHistory.application_id == application_id,
            ApplicationStatusHistory.to_status == closed_status,
        )
        .order_by(ApplicationStatusHistory.changed_at.desc(), ApplicationStatusHistory.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def history(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationStatusHistory]:
    """The Application's Status changes, newest first."""
    stmt = (
        select(ApplicationStatusHistory)
        .where(
            ApplicationStatusHistory.owner_id == owner_id,
            ApplicationStatusHistory.application_id == application_id,
        )
        .order_by(ApplicationStatusHistory.changed_at.desc(), ApplicationStatusHistory.id.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def delete_application(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID
) -> tuple[uuid.UUID | None] | None:
    """Delete one Application (its history cascades). Returns ``(listing_id,)``, or None
    when the owner has no such Application."""
    stmt = (
        delete(Application)
        .where(Application.owner_id == owner_id, Application.id == application_id)
        .returning(Application.listing_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    return (row[0],) if row is not None else None
