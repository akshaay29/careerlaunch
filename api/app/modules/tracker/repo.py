"""tracker.repo: SQL for ``applications`` and ``application_status_history`` only (AD-2).
Every function takes ``owner_id`` from the auth context (AD-5)."""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
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


# --------------------------------------------------------------------------- the Tracker (4.2)


@dataclass(frozen=True)
class ListFilters:
    """The Tracker filters (combined with AND). ``company`` is already trimmed. ``saved_from``
    / ``saved_before`` bound ``created_at`` as UTC instants (``>=`` / ``<``: the service turns
    the Student's local dates into them); ``applied_from`` / ``applied_to`` bound
    ``applied_on`` inclusively (rows without one are left out when a bound is given)."""

    statuses: tuple[str, ...] = ()
    company: str | None = None
    saved_from: datetime | None = None
    saved_before: datetime | None = None
    applied_from: date | None = None
    applied_to: date | None = None


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(owner_id: uuid.UUID, filters: ListFilters) -> list[Any]:
    clauses: list[Any] = [Application.owner_id == owner_id]
    if filters.statuses:
        clauses.append(Application.status.in_(filters.statuses))
    if filters.company:
        clauses.append(
            Application.company.ilike(f"%{_like_escape(filters.company)}%", escape="\\")
        )
    if filters.saved_from is not None:
        clauses.append(Application.created_at >= filters.saved_from)
    if filters.saved_before is not None:
        clauses.append(Application.created_at < filters.saved_before)
    if filters.applied_from is not None:
        clauses.append(Application.applied_on >= filters.applied_from)
    if filters.applied_to is not None:
        clauses.append(Application.applied_on <= filters.applied_to)
    return clauses


async def list_page(
    session: AsyncSession,
    owner_id: uuid.UUID,
    filters: ListFilters,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[Application]:
    """One page of the owner's Applications matching ``filters``, ordered
    ``created_at DESC, id DESC`` (immutable, so a row changed between page loads is never
    skipped or repeated); ``after`` is the exclusive keyset cursor."""
    stmt = select(Application).where(*_filtered(owner_id, filters))
    if after is not None:
        stmt = stmt.where(tuple_(Application.created_at, Application.id) < tuple_(*after))
    stmt = (
        stmt.order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return list((await session.execute(stmt)).scalars())


async def count(session: AsyncSession, owner_id: uuid.UUID, filters: ListFilters) -> int:
    """How many of the owner's Applications match ``filters`` (every page)."""
    stmt = select(func.count()).select_from(Application).where(*_filtered(owner_id, filters))
    return int((await session.execute(stmt)).scalar_one())


async def statuses_before_closing(
    session: AsyncSession, owner_id: uuid.UUID, closed: Sequence[tuple[uuid.UUID, str]]
) -> dict[uuid.UUID, str | None]:
    """Batch :func:`status_before_closing`: ``{application_id: from_status}`` of the latest
    history row that entered each Application's closed Status, in one query."""
    if not closed:
        return {}
    stmt = (
        select(
            ApplicationStatusHistory.application_id,
            ApplicationStatusHistory.to_status,
            ApplicationStatusHistory.from_status,
        )
        .where(
            ApplicationStatusHistory.owner_id == owner_id,
            tuple_(ApplicationStatusHistory.application_id, ApplicationStatusHistory.to_status).in_(
                list(closed)
            ),
        )
        .order_by(
            ApplicationStatusHistory.application_id,
            ApplicationStatusHistory.to_status,
            ApplicationStatusHistory.changed_at.desc(),
            ApplicationStatusHistory.id.desc(),
        )
        .ext(
            distinct_on(ApplicationStatusHistory.application_id, ApplicationStatusHistory.to_status)
        )
    )
    wanted = dict(closed)
    found: dict[uuid.UUID, str | None] = {}
    for application_id, to_status, from_status in (await session.execute(stmt)).all():
        if wanted.get(application_id) == to_status:
            found[application_id] = from_status
    return found


async def deadline_candidates(
    session: AsyncSession,
    owner_id: uuid.UUID,
    statuses: Collection[str],
    *,
    first: date,
    last: date,
    limit: int,
) -> list[Application]:
    """The owner's Applications in one of ``statuses`` whose deadline may fall in
    ``[first, last]``: those still referencing a Listing (its effective deadline is resolved
    by the caller) and those without one whose snapshot deadline is in range. At most
    ``limit`` rows, newest saved first."""
    stmt = (
        select(Application)
        .where(
            Application.owner_id == owner_id,
            Application.status.in_(list(statuses)),
            or_(
                Application.listing_id.is_not(None),
                Application.deadline.between(first, last),
            ),
        )
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return list((await session.execute(stmt)).scalars())


async def ids_for_listings(
    session: AsyncSession, owner_id: uuid.UUID, listing_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """``{listing_id: application_id}`` of the owner's Applications for ``listing_ids``."""
    if not listing_ids:
        return {}
    stmt = select(Application.listing_id, Application.id).where(
        Application.owner_id == owner_id, Application.listing_id.in_(list(listing_ids))
    )
    return {
        listing_id: application_id
        for listing_id, application_id in (await session.execute(stmt)).all()
        if listing_id is not None
    }
