"""listings.schemas: API DTOs."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import BaseModel

from app.core import skills
from app.ports.source_adapter import JobType

if TYPE_CHECKING:
    from app.modules.listings.models import Listing


class ListingSkill(BaseModel):
    """A canonical skill extracted from the Listing."""

    id: str
    label: str


class ListingOut(BaseModel):
    """One Listing. Every text field is scraped, untrusted text: render it as plain text.
    ``url`` is the source page ("Apply"); show ``source_label`` ("via LinkedIn") and the
    not-affiliated notice with it."""

    id: uuid.UUID
    title: str
    company: str
    location: str
    job_type: JobType | None
    posted_at: datetime | None
    deadline: date | None
    url: str
    source: str
    source_label: str
    skills: list[ListingSkill]
    first_seen_at: datetime

    @classmethod
    def from_listing(cls, listing: "Listing", source_label: str, **extra: Any) -> Self:
        """The DTO of a loaded Listing and its Source label; ``extra`` fills a subclass's own
        fields (the feed's ``score``)."""
        return cls(
            id=listing.id,
            title=listing.title,
            company=listing.company,
            location=listing.location,
            job_type=listing.job_type,  # type: ignore[arg-type]
            posted_at=listing.posted_at,
            deadline=listing.deadline,
            url=listing.url,
            source=listing.source,
            source_label=source_label,
            skills=[ListingSkill(id=s, label=skills.label(s)) for s in listing.skills],
            first_seen_at=listing.first_seen_at,
            **extra,
        )


class ListingList(BaseModel):
    """Newest first. Pass ``next_cursor`` as ``cursor`` to get the next page (null = done)."""

    items: list[ListingOut]
    next_cursor: str | None


class ListingSearchResults(BaseModel):
    """Best match first. Pass ``next_offset`` as ``offset`` to get the next page (null = no
    more; search shows the best matches, so offsets stop at 200)."""

    items: list[ListingOut]
    next_offset: int | None


class ProviderUsageStatus(BaseModel):
    """Whether collection runs for the Student now. ``reason`` (when paused):
    ``key_invalid``, ``credit_exhausted``, ``not_permitted`` or ``account_blocked``;
    ``exhausted_until``: when exhausted credit stops blocking (the billing-cycle end)."""

    status: Literal["active", "paused"]
    reason: str | None
    exhausted_until: datetime | None


class ProviderUsageCaps(BaseModel):
    per_run_usd: float
    monthly_usd: float
    is_default: bool


class ProviderUsageMonth(BaseModel):
    """This UTC calendar month: ``spent_usd`` recorded so far; ``committed_usd`` adds what
    runs in progress may still charge (counted against ``monthly_cap_usd``)."""

    spent_usd: float
    committed_usd: float
    monthly_cap_usd: float


class ProviderUsageRun(BaseModel):
    """One collection run. ``status``: ``starting``, ``running``, ``succeeded``, ``failed``
    or ``refused``; ``error_kind`` says why it failed or was refused."""

    created_at: datetime
    status: Literal["starting", "running", "succeeded", "failed", "refused"]
    error_kind: str | None
    estimated_cost_usd: float | None
    cost_usd: float | None
    items: int | None
    ingested: int | None


class ProviderUsage(BaseModel):
    """My collection status, caps, this month's spend and my latest 30 runs, newest first."""

    status: ProviderUsageStatus
    caps: ProviderUsageCaps
    month: ProviderUsageMonth
    runs: list[ProviderUsageRun]
