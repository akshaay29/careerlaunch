"""tracker.service: Applications and their Status (FR-16, AD-7, AD-20, Story 4.1).

Services take the session and never commit; routers do. Every function takes ``owner_id``
from the auth context, and another Student's Application is a 404 (AD-5).

:func:`transition` is the only writer of ``applications.status``. It applies
:data:`TRANSITIONS`, writes the history row and publishes ``ApplicationClosed`` /
``ApplicationReopened`` in the caller's transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import InvalidInputError, InvalidTransitionError, NotFoundError
from app.core.events import AccountDeleting, ApplicationClosed, ApplicationReopened, publish
from app.modules.listings import service as listings_service
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


async def _listing_available(session: AsyncSession, application: Application) -> bool:
    if application.listing_id is None:
        return False
    visible = await listings_service.visible_items(
        session, application.owner_id, [application.listing_id]
    )
    return bool(visible)


async def _view(session: AsyncSession, application: Application) -> ApplicationView:
    return ApplicationView(
        application,
        await allowed_next(session, application),
        await _listing_available(session, application),
        await _today(session, application.owner_id),
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


async def _today(session: AsyncSession, owner_id: uuid.UUID) -> date:
    """Today in the Student's Preferences time zone (default Asia/Kolkata)."""
    prefs = await profile_service.get_preferences(session, owner_id)
    return clock.local_day(prefs.timezone if prefs is not None else None)


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
