"""SourceAdapter port (AD-3, FR-7): where Listings come from.

A Source (LinkedIn via Apify in Story 2.3, Manual in Epic 4, ...) maps its raw output to
:class:`ListingDraft` and yields drafts; raw provider output is never stored. Every draft goes
through ``listings.service.ingest_draft``, which keeps only the minimal Listing fields and
discards the description (AD-7).

Callers pass the adapter in (no process-wide install yet). In-memory fake:
``tests/fakes/source_adapter.py``.
"""

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

# Limits shared with the ``listings`` CHECK constraints (migration 0011_listings).
MAX_TITLE_CHARS = 300
MAX_COMPANY_CHARS = 200
MAX_LOCATION_CHARS = 200
MAX_SOURCE_JOB_ID_CHARS = 200
MAX_DESCRIPTION_CHARS = 100_000
MAX_URL_CHARS = 2048

SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

JobType = Literal["internship", "full_time"]

# Same rule as the web's ``safeExternalUrl``: no control characters, whitespace or backslash.
_URL_UNSAFE = re.compile(r"[\x00-\x1f\x7f\s\\]")


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("text must not contain NUL characters")
    return value


def _http_url(value: str) -> str:
    """Only absolute ``http(s)`` URLs with a host; no whitespace, backslash or control
    characters."""
    if _URL_UNSAFE.search(value):
        raise ValueError("url must not contain whitespace, backslashes or control characters")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError("url is not a valid URL") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an absolute http(s) URL")
    return value


def _text(max_length: int, min_length: int = 0) -> type[str]:
    return Annotated[  # type: ignore[return-value]
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
        AfterValidator(_no_nul),
    ]


Title = _text(MAX_TITLE_CHARS, 1)
Company = _text(MAX_COMPANY_CHARS)
Location = _text(MAX_LOCATION_CHARS)
SourceJobId = _text(MAX_SOURCE_JOB_ID_CHARS, 1)
Description = _text(MAX_DESCRIPTION_CHARS)
SourceId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=SOURCE_ID_PATTERN)]
HttpUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_URL_CHARS),
    AfterValidator(_http_url),
]


class ListingDraft(BaseModel):
    """One Listing as a Source sees it, before ingestion. Text is stripped and length-capped;
    an over-length field or a non-http(s) URL is a ``ValidationError`` (nothing is written).

    ``description`` is used only during ingestion (skills and search lexemes) and is never
    stored or logged. ``repr`` hides it so a stray log line cannot leak it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceId
    source_job_id: SourceJobId
    url: HttpUrl
    title: Title
    company_raw: Company = ""
    location_raw: Location = ""
    job_type: JobType | None = None
    description: Description = Field(default="", repr=False)
    posted_at: datetime | None = None
    deadline: date | None = None

    @field_validator("posted_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        """A naive ``posted_at`` is taken as UTC."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class SourceQuery(BaseModel):
    """What to collect: scoped to a Student's Preferences (Story 2.3) or a seed run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role_titles: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    job_types: tuple[JobType, ...] = ()
    limit: int = Field(default=100, ge=1, le=1000)


@runtime_checkable
class SourceAdapter(Protocol):
    """A Source of Listings. ``source`` is the ``sources.id`` it collects for."""

    source: str

    def collect(self, query: SourceQuery) -> AsyncIterator[ListingDraft]:
        """Yield the drafts matching ``query`` (at most ``query.limit``)."""
        ...
