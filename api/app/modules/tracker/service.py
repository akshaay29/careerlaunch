"""tracker.service: Applications and their Status (FR-16, AD-7, AD-20, Story 4.1).

Services take the session and never commit; routers do. Every function takes ``owner_id``
from the auth context, and another Student's Application is a 404 (AD-5).

:func:`transition` is the only writer of ``applications.status``. It applies
:data:`TRANSITIONS`, writes the history row and publishes ``ApplicationClosed`` /
``ApplicationReopened`` in the caller's transaction.
"""

import base64
import binascii
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import InvalidInputError, InvalidTransitionError, NotFoundError
from app.core.events import AccountDeleting, ApplicationClosed, ApplicationReopened, publish
from app.modules.listings import service as listings_service
from app.modules.matching import service as matching_service
from app.modules.profile import service as profile_service
from app.modules.tracker import repo
from app.modules.tracker.models import Application, ApplicationStatusHistory

NOT_FOUND_DETAIL = "No such Application."
MAX_NOTES = 4000
# The earliest applied date accepted (a guard against typos such as year 0026).
MIN_APPLIED_ON = date(2000, 1, 1)

SAVED = "saved"
APPLIED = "applied"
INTERVIEW = "interview"
OFFER = "offer"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
STATUSES = (SAVED, APPLIED, INTERVIEW, OFFER, REJECTED, WITHDRAWN)
CLOSED = frozenset({REJECTED, WITHDRAWN})

# The forward moves. A closed Status (Rejected, Withdrawn) reopens only to the Status it was
# closed from (read from history); Offer is terminal.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    SAVED: (APPLIED, WITHDRAWN),
    APPLIED: (INTERVIEW, REJECTED, WITHDRAWN),
    INTERVIEW: (OFFER, REJECTED, WITHDRAWN),
    OFFER: (),
}


class _Unset:
    """Marks a field left out of a partial update (``None`` means "clear it")."""


UNSET: Any = _Unset()


@dataclass(frozen=True)
class ApplicationView:
    """An Application with the Statuses it may move to next, whether its Listing is still
    there for the Student (exists, visible to them, not expired), and their local today."""

    application: Application
    allowed_next: tuple[str, ...]
    listing_available: bool
    today: date
    # The effective deadline while the Listing is available (``listings.service``), else the
    # snapshot deadline (AD-7).
    deadline: date | None


async def _owned(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID, *, lock: bool = False
) -> Application:
    application = await repo.get(session, owner_id, application_id, lock=lock)
    if application is None:
        # Another Student's id and an unknown id look the same (AD-5).
        raise NotFoundError(NOT_FOUND_DETAIL)
    return application


async def allowed_next(session: AsyncSession, application: Application) -> tuple[str, ...]:
    """The Statuses ``application`` may move to now. A closed Application may only go back
    to the Status it was closed from: the ``from_status`` of the latest history row that
    entered the closed Status."""
    if application.status in CLOSED:
        previous = await repo.status_before_closing(
            session, application.owner_id, application.id, application.status
        )
        return (previous,) if previous is not None else ()
    return TRANSITIONS.get(application.status, ())


async def _available_deadlines(
    session: AsyncSession, owner_id: uuid.UUID, applications: Iterable[Application]
) -> dict[uuid.UUID, date | None]:
    """``{listing_id: effective deadline}`` of the Listings of ``applications`` still
    available to the owner (exist, visible to them, not expired), in one query (none when no
    Application references a Listing). An absent Listing is not available."""
    listing_ids = {a.listing_id for a in applications if a.listing_id is not None}
    if not listing_ids:
        return {}
    return await listings_service.effective_deadlines(session, owner_id, listing_ids)


def _deadline(application: Application, available: dict[uuid.UUID, date | None]) -> date | None:
    """The Listing's effective deadline while it is available and has one, else the snapshot
    deadline (AD-7): a Listing that lost its deadline never hides the snapshot's."""
    if application.listing_id is not None:
        effective = available.get(application.listing_id)
        if effective is not None:
            return effective
    return application.deadline


async def _view(session: AsyncSession, application: Application) -> ApplicationView:
    available = await _available_deadlines(session, application.owner_id, [application])
    return ApplicationView(
        application,
        await allowed_next(session, application),
        application.listing_id is not None and application.listing_id in available,
        await _today(session, application.owner_id),
        _deadline(application, available),
    )


async def save_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> tuple[ApplicationView, bool]:
    """Save a Listing the Student can see as an Application in Status Saved, with a snapshot
    of the Listing, a history row and a pin. Returns ``(view, created)``. A Listing already
    saved returns that Application unchanged and is not pinned again. A Listing that is not
    visible, expired or unknown raises :class:`NotFoundError` and writes nothing."""
    # FOR KEY SHARE: the Listing cannot be deleted between this check and the insert.
    if not await listings_service.lock_existing(session, [listing_id]):
        raise NotFoundError(listings_service.NOT_FOUND_DETAIL)
    item = await listings_service.get_listing(session, owner_id, listing_id)
    existing = await repo.get_by_listing(session, owner_id, listing_id)
    if existing is not None:
        return await _view(session, existing), False
    listing = item.listing
    now = clock.utcnow()
    application = await repo.insert_application(
        session,
        owner_id=owner_id,
        listing_id=listing_id,
        status=SAVED,
        title=listing.title,
        company=listing.company,
        url=listing.url,
        deadline=listing.deadline,
        now=now,
    )
    if application is None:
        # A concurrent save of the same Listing won the unique key; it holds the pin.
        existing = await repo.get_by_listing(session, owner_id, listing_id)
        if existing is None:
            raise NotFoundError(NOT_FOUND_DETAIL)
        return await _view(session, existing), False
    await repo.insert_history(
        session,
        application_id=application.id,
        owner_id=owner_id,
        from_status=None,
        to_status=SAVED,
        changed_at=now,
    )
    await listings_service.pin(session, listing_id)
    return await _view(session, application), True


async def _timezone(session: AsyncSession, owner_id: uuid.UUID) -> str:
    """The Student's Preferences time zone as a valid IANA name (default Asia/Kolkata)."""
    prefs = await profile_service.get_preferences(session, owner_id)
    return clock.zone(prefs.timezone if prefs is not None else None).key


async def _today(session: AsyncSession, owner_id: uuid.UUID) -> date:
    """Today in the Student's Preferences time zone (default Asia/Kolkata)."""
    return clock.local_day(await _timezone(session, owner_id))


async def transition(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID, to_status: str
) -> ApplicationView:
    """Move an Application to ``to_status`` if the transition table allows it, else raise
    :class:`InvalidTransitionError` (409) naming the allowed next Statuses; the same Status is
    never allowed. The row is locked (``FOR UPDATE``) so concurrent transitions serialise.
    Writes the history row, bumps ``updated_at``, sets ``applied_on`` to today on entering
    Applied when it is empty, and publishes ``ApplicationClosed`` / ``ApplicationReopened``,
    all in the caller's transaction."""
    application = await _owned(session, owner_id, application_id, lock=True)
    current = application.status
    allowed = await allowed_next(session, application)
    if to_status not in allowed:
        raise InvalidTransitionError(current, to_status, list(allowed))

    now = clock.utcnow()
    values: dict[str, Any] = {"status": to_status, "updated_at": now}
    if to_status == APPLIED and application.applied_on is None:
        values["applied_on"] = await _today(session, owner_id)
    application = await repo.update_application(session, owner_id, application_id, **values)
    await repo.insert_history(
        session,
        application_id=application_id,
        owner_id=owner_id,
        from_status=current,
        to_status=to_status,
        changed_at=now,
    )
    if to_status in CLOSED:
        await publish(
            session,
            ApplicationClosed(
                owner_id=owner_id,
                application_id=application_id,
                listing_id=application.listing_id,
            ),
        )
    elif current in CLOSED:
        await publish(
            session,
            ApplicationReopened(
                owner_id=owner_id,
                application_id=application_id,
                listing_id=application.listing_id,
            ),
        )
    return await _view(session, application)


async def update_details(
    session: AsyncSession,
    owner_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    notes: str | None = UNSET,
    applied_on: date | None = UNSET,
    resume_id: uuid.UUID | None = UNSET,
) -> ApplicationView:
    """Partially update the notes, the applied date and the Resume version used; a field
    left :data:`UNSET` is kept. Never changes ``status``. Notes are trimmed (empty clears
    them; over 4,000 characters is a 422). The applied date may not be after today in the
    Student's time zone (422). The Resume must be one of the Student's own (422); None clears
    it."""
    application = await _owned(session, owner_id, application_id, lock=True)
    values: dict[str, Any] = {}
    if notes is not UNSET:
        text = notes.strip() if notes is not None else ""
        if len(text) > MAX_NOTES:
            raise InvalidInputError(
                ["body", "notes"], f"Notes must be {MAX_NOTES} characters or fewer."
            )
        values["notes"] = text or None
    if applied_on is not UNSET:
        if applied_on is not None and applied_on > await _today(session, owner_id):
            raise InvalidInputError(
                ["body", "applied_on"], "The applied date cannot be in the future."
            )
        if applied_on is not None and applied_on < MIN_APPLIED_ON:
            raise InvalidInputError(
                ["body", "applied_on"], "The applied date cannot be before 1 Jan 2000."
            )
        values["applied_on"] = applied_on
    if resume_id is not UNSET:
        if resume_id is not None and not await profile_service.owns_resume(
            session, owner_id, resume_id
        ):
            raise InvalidInputError(["body", "resume_id"], "No such Resume version.")
        values["resume_id"] = resume_id
    if values:
        values["updated_at"] = clock.utcnow()
        try:
            async with session.begin_nested():
                application = await repo.update_application(
                    session, owner_id, application_id, **values
                )
        except IntegrityError:
            # The Resume was deleted between the ownership check and the write.
            raise InvalidInputError(["body", "resume_id"], "No such Resume version.") from None
    return await _view(session, application)


async def delete_application(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID
) -> None:
    """Delete an Application (its history cascades) and release its pin on the Listing when
    the Listing still exists. An Application that was not already closed publishes
    ``ApplicationClosed`` first, in the same transaction."""
    application = await _owned(session, owner_id, application_id, lock=True)
    if application.status not in CLOSED:
        await publish(
            session,
            ApplicationClosed(
                owner_id=owner_id,
                application_id=application_id,
                listing_id=application.listing_id,
            ),
        )
    deleted = await repo.delete_application(session, owner_id, application_id)
    if deleted is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    (listing_id,) = deleted
    if listing_id is not None:
        await _unpin_existing(session, [listing_id])


async def _unpin_existing(session: AsyncSession, listing_ids: list[uuid.UUID]) -> None:
    """Release one pin on each Listing that still exists (each locked first)."""
    for listing_id in await listings_service.lock_existing(session, listing_ids):
        await listings_service.unpin(session, listing_id)


async def on_account_deleting(session: AsyncSession, event: AccountDeleting) -> None:
    """The account is being deleted and its Applications go with it (cascade): release
    their Listing pins in the deleting transaction, so those Listings can be evicted again."""
    await _unpin_existing(session, await repo.listing_ids(session, event.owner_id))


async def get_application(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID
) -> ApplicationView:
    return await _view(session, await _owned(session, owner_id, application_id))


async def history(
    session: AsyncSession, owner_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationStatusHistory]:
    """The Application's Status changes, newest first."""
    await _owned(session, owner_id, application_id)
    return await repo.history(session, owner_id, application_id)


async def application_for_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> uuid.UUID | None:
    """The id of the owner's Application for ``listing_id``, or None."""
    application = await repo.get_by_listing(session, owner_id, listing_id)
    return application.id if application is not None else None


# --------------------------------------------------------------------------- the Tracker (4.2)

INVALID_CURSOR = "The cursor is not valid. Start again without a cursor."
MAX_COMPANY_CHARS = 100
DEFAULT_PAGE = 20
MAX_PAGE = 50
DEFAULT_DEADLINE_DAYS = 30
MAX_DEADLINE_DAYS = 180
MAX_UPCOMING = 20
# The most open Applications read for the deadlines section (newest saved first).
MAX_DEADLINE_CANDIDATES = 500
# Statuses whose deadline still matters.
DEADLINE_STATUSES = (SAVED, APPLIED, INTERVIEW)
MAX_LISTING_IDS = 50

DateBy = Literal["saved", "applied"]


@dataclass(frozen=True)
class TrackerItem:
    """One Tracker row: the Application view plus the Student's STORED Match Score of its
    Listing (None when there is none or the Listing is no longer available)."""

    view: ApplicationView
    score: int | None


@dataclass(frozen=True)
class TrackerPage:
    items: list[TrackerItem]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class UpcomingDeadline:
    application: Application
    deadline: date
    days_left: int
    in_pool: bool


def encode_cursor(application: Application) -> str:
    raw = f"{application.created_at.isoformat()}|{application.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """The ``(created_at, id)`` keyset of an opaque cursor; 422 when it is not one."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, application_id = raw.split("|")
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(application_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInputError(["query", "cursor"], INVALID_CURSOR) from exc


def _company_filter(company: str | None) -> str | None:
    if company is None:
        return None
    text = company.strip()
    if not 1 <= len(text) <= MAX_COMPANY_CHARS:
        raise InvalidInputError(
            ["query", "company"],
            f"The company filter must be 1 to {MAX_COMPANY_CHARS} characters.",
        )
    return text


async def _allowed_next_many(
    session: AsyncSession, owner_id: uuid.UUID, applications: list[Application]
) -> dict[uuid.UUID, tuple[str, ...]]:
    """Batch :func:`allowed_next`: at most one history query for the whole page."""
    closed = [(a.id, a.status) for a in applications if a.status in CLOSED]
    previous = await repo.statuses_before_closing(session, owner_id, closed)
    allowed: dict[uuid.UUID, tuple[str, ...]] = {}
    for a in applications:
        if a.status in CLOSED:
            before = previous.get(a.id)
            allowed[a.id] = (before,) if before is not None else ()
        else:
            allowed[a.id] = TRANSITIONS.get(a.status, ())
    return allowed


def _local_midnight(day: date, timezone: str) -> datetime:
    """The UTC instant at which ``day`` starts in ``timezone`` (an unknown zone falls back to
    the default, as everywhere else)."""
    try:
        return datetime.combine(day, time(0), clock.zone(timezone)).astimezone(UTC)
    except OverflowError:
        raise InvalidInputError(["query", "date_from"], "The date is out of range.") from None


async def list_applications(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    statuses: Iterable[str] = (),
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    date_by: DateBy = "saved",
    limit: int = DEFAULT_PAGE,
    cursor: str | None = None,
) -> TrackerPage:
    """The Student's Applications matching every given filter, ``created_at DESC, id DESC``,
    with ``total`` counting all matching rows. ``company`` is a case-insensitive substring of
    the snapshot company (trimmed, 1 to 100 characters, else 422). The date range (inclusive)
    applies to the save date in the Student's time zone (``saved``) or to ``applied_on``
    (``applied``); ``date_from`` after ``date_to`` is a 422, as is an invalid cursor.

    Each row carries the effective deadline while its Listing is available (else the
    snapshot) and the STORED Match Score of an available Listing (never computed here). A
    fixed number of queries per page, never one per row: preferences, the page, the count,
    closed-Status history, Listing availability and deadlines, and stored scores."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidInputError(["query", "date_from"], "The start date is after the end date.")
    after = decode_cursor(cursor) if cursor else None
    limit = max(1, min(limit, MAX_PAGE))
    timezone = await _timezone(session, owner_id)
    saved = date_by == "saved"
    filters = repo.ListFilters(
        statuses=tuple(dict.fromkeys(statuses)),
        company=_company_filter(company),
        saved_from=_local_midnight(date_from, timezone) if saved and date_from else None,
        saved_before=(
            _local_midnight(date_to + timedelta(days=1), timezone)
            if saved and date_to and date_to < date.max
            else None
        ),
        applied_from=date_from if not saved else None,
        applied_to=date_to if not saved else None,
    )
    rows = await repo.list_page(session, owner_id, filters, limit=limit + 1, after=after)
    page = rows[:limit]
    total = await repo.count(session, owner_id, filters)
    allowed = await _allowed_next_many(session, owner_id, page)
    available = await _available_deadlines(session, owner_id, page)
    scores = (
        await matching_service.stored_scores(session, owner_id, available) if available else {}
    )
    today = clock.local_day(timezone)
    items = []
    for a in page:
        in_pool = a.listing_id is not None and a.listing_id in available
        view = ApplicationView(a, allowed[a.id], in_pool, today, _deadline(a, available))
        score = scores.get(a.listing_id) if in_pool and a.listing_id is not None else None
        items.append(TrackerItem(view, score))
    next_cursor = encode_cursor(page[-1]) if len(rows) > limit else None
    return TrackerPage(items, total, next_cursor)


async def upcoming_deadlines(
    session: AsyncSession, owner_id: uuid.UUID, *, days: int = DEFAULT_DEADLINE_DAYS
) -> list[UpcomingDeadline]:
    """The Student's Saved, Applied and Interview Applications whose deadline (effective
    while the Listing is available, else the snapshot) falls from today to today + ``days``
    (1 to 180, else 422) in their time zone, by deadline then title, at most 20. SQL drops
    snapshot-only rows outside the window and reads at most 500 candidates (newest saved
    first) before one effective-deadline lookup."""
    if not 1 <= days <= MAX_DEADLINE_DAYS:
        raise InvalidInputError(["query", "days"], f"Days must be 1 to {MAX_DEADLINE_DAYS}.")
    today = await _today(session, owner_id)
    last = today + timedelta(days=days)
    applications = await repo.deadline_candidates(
        session,
        owner_id,
        DEADLINE_STATUSES,
        first=today,
        last=last,
        limit=MAX_DEADLINE_CANDIDATES,
    )
    available = await _available_deadlines(session, owner_id, applications)
    upcoming = []
    for a in applications:
        deadline = _deadline(a, available)
        if deadline is not None and today <= deadline <= last:
            in_pool = a.listing_id is not None and a.listing_id in available
            upcoming.append(UpcomingDeadline(a, deadline, (deadline - today).days, in_pool))
    upcoming.sort(key=lambda u: (u.deadline, u.application.title.casefold(), u.application.id))
    return upcoming[:MAX_UPCOMING]


async def applications_for_listings(
    session: AsyncSession, owner_id: uuid.UUID, listing_ids: Iterable[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(listing_id, application_id)`` for each given Listing the Student has saved, in
    request order. At most 50 distinct ids (de-duplicated first), else 422."""
    ids = list(dict.fromkeys(listing_ids))
    if len(ids) > MAX_LISTING_IDS:
        raise InvalidInputError(
            ["query", "ids"], f"Ask for at most {MAX_LISTING_IDS} Listings at a time."
        )
    found = await repo.ids_for_listings(session, owner_id, ids)
    return [(lid, found[lid]) for lid in ids if lid in found]
