"""tracker.schemas: API DTOs for Applications (Story 4.1)."""

import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

from app.modules.tracker.models import ApplicationStatusHistory
from app.modules.tracker.service import MAX_NOTES, ApplicationView

ApplicationStatus = Literal["saved", "applied", "interview", "offer", "rejected", "withdrawn"]


class SaveListingIn(BaseModel):
    listing_id: uuid.UUID


class ApplicationOut(BaseModel):
    """One Application. ``title``, ``company``, ``url`` and ``deadline`` are the snapshot taken
    when the Listing was saved (scraped, untrusted text: render it as plain text).
    ``listing_available`` is false once the Listing is gone, expired or no longer visible to
    you. ``allowed_next`` lists the Statuses it may move to now (empty when none). ``today``
    is your local date (Preferences time zone): the latest allowed ``applied_on``."""

    id: uuid.UUID
    listing_id: uuid.UUID | None
    listing_available: bool
    status: ApplicationStatus
    allowed_next: list[ApplicationStatus]
    title: str
    company: str
    url: str
    deadline: date | None
    notes: str | None
    applied_on: date | None
    resume_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    today: date

    @classmethod
    def from_view(cls, view: ApplicationView) -> Self:
        a = view.application
        return cls(
            id=a.id,
            listing_id=a.listing_id,
            listing_available=view.listing_available,
            status=a.status,  # type: ignore[arg-type]
            allowed_next=list(view.allowed_next),  # type: ignore[arg-type]
            title=a.title,
            company=a.company,
            url=a.url,
            deadline=a.deadline,
            notes=a.notes,
            applied_on=a.applied_on,
            resume_id=a.resume_id,
            created_at=a.created_at,
            updated_at=a.updated_at,
            today=view.today,
        )


class ApplicationUpdate(BaseModel):
    """A partial update: only the fields sent are changed. ``notes`` is trimmed (empty clears
    it, 4,000 characters at most); ``applied_on`` is from 2000-01-01 to today; ``resume_id`` must
    be one of your Resume versions, and null clears it."""

    # The limit applies after trimming (service); this cap leaves room for the whitespace.
    notes: str | None = Field(default=None, max_length=MAX_NOTES + 200)
    applied_on: date | None = None
    resume_id: uuid.UUID | None = None


class TransitionIn(BaseModel):
    status: ApplicationStatus


class StatusChange(BaseModel):
    """One Status change. ``from_status`` is null for the save."""

    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    changed_at: datetime

    @classmethod
    def from_row(cls, row: ApplicationStatusHistory) -> Self:
        return cls(
            from_status=row.from_status,  # type: ignore[arg-type]
            to_status=row.to_status,  # type: ignore[arg-type]
            changed_at=row.changed_at,
        )


class ApplicationHistory(BaseModel):
    """Status changes, newest first."""

    items: list[StatusChange]


class ApplicationForListing(BaseModel):
    """Your Application for a Listing, or null when you have not saved it."""

    application_id: uuid.UUID | None
