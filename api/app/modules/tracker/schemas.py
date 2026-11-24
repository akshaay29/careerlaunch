"""tracker.schemas: API DTOs for Applications (Story 4.1)."""

import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

from app.modules.listings.service import MAX_MANUAL_DESCRIPTION_CHARS
from app.modules.tracker.models import ApplicationStatusHistory
from app.modules.tracker.service import (
    MAX_NOTES,
    ApplicationView,
    TrackerItem,
    UpcomingDeadline,
)
from app.ports.source_adapter import (
    MAX_COMPANY_CHARS,
    MAX_LOCATION_CHARS,
    MAX_TITLE_CHARS,
    MAX_URL_CHARS,
    JobType,
)

ApplicationStatus = Literal["saved", "applied", "interview", "offer", "rejected", "withdrawn"]


class SaveListingIn(BaseModel):
    listing_id: uuid.UUID


class ApplicationOut(BaseModel):
    """One Application. ``title``, ``company`` and ``url`` are the snapshot taken when the
    Listing was saved (scraped, untrusted text: render it as plain text); ``url`` is null for
    a job you added without a link. ``deadline`` is the Listing's current deadline while it is
    available to you, else the snapshot. ``listing_available`` is false once the Listing is
    gone, expired or no longer visible to you. ``allowed_next`` lists the Statuses it may move
    to now (empty when none). ``today`` is your local date (Preferences time zone): the latest
    allowed ``applied_on``. ``is_manual`` is true for a job you added from another site
    ("Added by you"); ``description`` is the description you gave it (plain text), null
    otherwise."""

    id: uuid.UUID
    listing_id: uuid.UUID | None
    listing_available: bool
    status: ApplicationStatus
    allowed_next: list[ApplicationStatus]
    title: str
    company: str
    url: str | None
    deadline: date | None
    notes: str | None
    applied_on: date | None
    resume_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    today: date
    is_manual: bool
    description: str | None

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
            deadline=view.deadline,
            notes=a.notes,
            applied_on=a.applied_on,
            resume_id=a.resume_id,
            created_at=a.created_at,
            updated_at=a.updated_at,
            today=view.today,
            is_manual=view.manual,
            description=view.description,
        )


class ExternalJobIn(BaseModel):
    """A job found on another site. Give a ``title`` or a ``url`` (without a title it is
    "Job at <host>"). ``url`` must be an absolute http(s) link: it is stored so you can open it
    later and is never visited by the server. ``description`` (at most 4,000 characters) is
    kept private to you. ``deadline`` is not before 2000-01-01. Text is trimmed."""

    # The exact limits apply after trimming (service); these caps leave room for whitespace.
    url: str | None = Field(default=None, max_length=MAX_URL_CHARS + 200)
    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS + 200)
    company: str | None = Field(default=None, max_length=MAX_COMPANY_CHARS + 200)
    location: str | None = Field(default=None, max_length=MAX_LOCATION_CHARS + 200)
    job_type: JobType | None = None
    deadline: date | None = None
    description: str | None = Field(default=None, max_length=MAX_MANUAL_DESCRIPTION_CHARS + 200)


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


class TrackerItemOut(ApplicationOut):
    """One Tracker row. ``deadline`` is the Listing's current (effective) deadline while it is
    available to you, else the snapshot. ``in_pool`` equals ``listing_available``. ``score``
    is your stored Match Score of the Listing (null when none is stored or the Listing is no
    longer available). ``description`` is always null here: open the Application for it."""

    in_pool: bool
    score: int | None

    @classmethod
    def from_item(cls, item: TrackerItem) -> Self:
        base = ApplicationOut.from_view(item.view).model_dump()
        return cls(**base, in_pool=item.view.listing_available, score=item.score)


class TrackerPageOut(BaseModel):
    """One page of your Applications, most recently saved first. ``total`` counts every
    Application matching the filters; ``next_cursor`` is set when more follow."""

    items: list[TrackerItemOut]
    total: int
    next_cursor: str | None


class UpcomingDeadlineOut(BaseModel):
    """An open Application (Saved, Applied or Interview) with a deadline coming up.
    ``days_left`` is 0 for today, in your time zone."""

    id: uuid.UUID
    title: str
    company: str
    status: ApplicationStatus
    deadline: date
    days_left: int
    in_pool: bool

    @classmethod
    def from_upcoming(cls, u: UpcomingDeadline) -> Self:
        a = u.application
        return cls(
            id=a.id,
            title=a.title,
            company=a.company,
            status=a.status,  # type: ignore[arg-type]
            deadline=u.deadline,
            days_left=u.days_left,
            in_pool=u.in_pool,
        )


class UpcomingDeadlines(BaseModel):
    """Upcoming deadlines, soonest first (then by title), at most 20."""

    items: list[UpcomingDeadlineOut]


class SavedListing(BaseModel):
    listing_id: uuid.UUID
    application_id: uuid.UUID


class SavedListings(BaseModel):
    """Your Applications for the asked Listings; unsaved Listings are left out."""

    items: list[SavedListing]
