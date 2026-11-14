"""listings.schemas: API DTOs."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.ports.source_adapter import JobType


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


class ListingList(BaseModel):
    """Newest first. Pass ``next_cursor`` as ``cursor`` to get the next page (null = done)."""

    items: list[ListingOut]
    next_cursor: str | None
