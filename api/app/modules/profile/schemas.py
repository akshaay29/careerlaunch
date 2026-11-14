"""profile.schemas: API DTOs."""

import unicodedata
import uuid
import zoneinfo
from datetime import datetime
from functools import cache
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.core import degrees
from app.modules.profile.files import DocumentContentType, ResumeContentType

# Limits on Skill Profile edits (FR-4).
MAX_SKILL_TERM_CHARS = 50
MAX_CUSTOM_SKILLS = 50


class ResumeVersion(BaseModel):
    """One uploaded Resume version. ``filename`` is untrusted text: render it as plain text."""

    id: uuid.UUID
    filename: str
    content_type: ResumeContentType
    size_bytes: int
    is_active: bool
    created_at: datetime


class ResumeList(BaseModel):
    """Newest first. Pass ``next_cursor`` as ``cursor`` to get the next page (null = done)."""

    items: list[ResumeVersion]
    next_cursor: uuid.UUID | None


class ResumeDownload(BaseModel):
    """A signed URL that downloads the file as an attachment; it stops working at
    ``expires_at`` (5 minutes or less)."""

    url: str
    expires_at: datetime


# --------------------------------------------------------------------------- Documents


class DocumentOut(BaseModel):
    """One stored Document. ``filename`` is untrusted text: render it as plain text."""

    id: uuid.UUID
    filename: str
    content_type: DocumentContentType
    size_bytes: int
    created_at: datetime


class DocumentList(BaseModel):
    """Newest first. Pass ``next_cursor`` as ``cursor`` to get the next page (null = done)."""

    items: list[DocumentOut]
    next_cursor: uuid.UUID | None


class DocumentDownload(BaseModel):
    """A signed URL that downloads the file as an attachment; it stops working at
    ``expires_at`` (5 minutes or less)."""

    url: str
    expires_at: datetime


# --------------------------------------------------------------------------- Skill Profile

SkillProfileStatus = Literal["none", "pending", "ready", "unreadable", "failed"]
SkillSource = Literal["extracted", "added"]

_SKILL_ID_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def _plain_term(value: str) -> str:
    term = " ".join(value.split())
    if not term:
        raise ValueError("must not be blank")
    if len(term) > MAX_SKILL_TERM_CHARS:
        raise ValueError(f"must be at most {MAX_SKILL_TERM_CHARS} characters")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp") for ch in term):
        raise ValueError("must not contain control characters")
    return term


SkillTerm = Annotated[str, AfterValidator(_plain_term)]
SkillId = Annotated[str, StringConstraints(pattern=_SKILL_ID_RE, max_length=64)]


class SkillRef(BaseModel):
    id: str
    label: str


class ProfileSkill(BaseModel):
    """An effective skill. ``source`` is ``extracted`` when the active Resume mentions it,
    else ``added`` (the Student added it)."""

    id: str
    label: str
    source: SkillSource


class SkillProfileOut(BaseModel):
    """The Student's Skill Profile. ``status`` is ``none`` before any Resume was extracted.
    ``message`` is set when the Resume could not be read. ``custom``, ``education`` and
    ``experience`` are untrusted text: render them as plain text. ``resume_id`` is null when
    the source Resume was deleted (the profile is kept)."""

    status: SkillProfileStatus
    resume_id: uuid.UUID | None
    skills: list[ProfileSkill]
    removed: list[SkillRef]
    custom: list[str]
    education: list[str]
    experience: list[str]
    vocab_version: str | None
    message: str | None
    # True when the Student added, removed or entered custom skills ("Reset to extracted"
    # would change something).
    has_edits: bool


class SkillProfileEdit(BaseModel):
    """``add``: terms (a vocabulary match becomes a skill, anything else a custom skill);
    ``remove``: skill ids; ``remove_custom``: custom terms."""

    model_config = ConfigDict(extra="forbid")

    add: list[SkillTerm] = Field(default_factory=list, max_length=MAX_CUSTOM_SKILLS)
    remove: list[SkillId] = Field(default_factory=list, max_length=500)
    remove_custom: list[SkillTerm] = Field(default_factory=list, max_length=MAX_CUSTOM_SKILLS)


# --------------------------------------------------------------------------- Preferences

# Limits on Preferences (FR-5); the same limits are CHECK constraints (0008_preferences).
MAX_PREFERENCE_ITEMS = 10
MAX_PREFERENCE_ITEM_CHARS = 80
# Raw list size accepted before blanks and duplicates are dropped.
MAX_RAW_PREFERENCE_ITEMS = 50

JobType = Literal["internship", "full_time"]
AlertFrequency = Literal["daily", "weekly", "off"]
WorkAuthorisation = Literal["india_authorised", "needs_sponsorship"]
JOB_TYPE_ORDER: tuple[JobType, ...] = ("internship", "full_time")


# Bidi embedding / override / isolate controls (U+202A–U+202E, U+2066–U+2069).
_BIDI_CONTROLS = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))


def _has_control_chars(text: str) -> bool:
    """Control characters (Cc) and bidi controls. Other format characters stay allowed:
    ZWJ / ZWNJ (U+200D / U+200C) are needed by Indic scripts."""
    return any(unicodedata.category(ch) == "Cc" or ch in _BIDI_CONTROLS for ch in text)


def _clean_item(value: str) -> str:
    """One role title or location: whitespace trimmed and collapsed. Blank is allowed here
    (the list drops it)."""
    item = " ".join(value.split())
    if len(item) > MAX_PREFERENCE_ITEM_CHARS:
        raise ValueError(f"must be at most {MAX_PREFERENCE_ITEM_CHARS} characters")
    if _has_control_chars(item):
        raise ValueError("must not contain control characters")
    return item


def _clean_items(values: list[str]) -> list[str]:
    """Blanks dropped, duplicates (ignoring case) dropped keeping the first, at most 10."""
    seen: set[str] = set()
    items: list[str] = []
    for item in values:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            items.append(item)
    if len(items) > MAX_PREFERENCE_ITEMS:
        raise ValueError(f"must have at most {MAX_PREFERENCE_ITEMS} items")
    return items


PreferenceItems = Annotated[
    list[Annotated[str, AfterValidator(_clean_item)]],
    Field(max_length=MAX_RAW_PREFERENCE_ITEMS),
    AfterValidator(_clean_items),
]


def _job_types(values: list[JobType]) -> list[JobType]:
    """Canonical order, no duplicates. Empty means any job type."""
    return [t for t in JOB_TYPE_ORDER if t in values]


def _degree_id(value: str) -> str:
    if not degrees.is_degree_id(value):
        raise ValueError("must be a degree id from GET /degrees")
    return value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value or None
    return value


def _plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > MAX_PREFERENCE_ITEM_CHARS:
        raise ValueError(f"must be at most {MAX_PREFERENCE_ITEM_CHARS} characters")
    if _has_control_chars(value):
        raise ValueError("must not contain control characters")
    return value


@cache
def _timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def _timezone(value: str) -> str:
    if value not in _timezones():
        raise ValueError("must be an IANA time zone name, such as Asia/Kolkata")
    return value


class PreferencesFields(BaseModel):
    """The Student's Preferences (FR-5). ``job_types`` empty means any. A null eligibility
    field (``batch_year``, ``cgpa``, ``degree``, ``work_authorisation``) means not stated.
    ``degree_other`` is set only when ``degree`` is ``other``. ``timezone`` is an IANA name
    and defines the Student's day."""

    role_titles: list[str]
    locations: list[str]
    job_types: list[JobType]
    batch_year: int | None
    cgpa: float | None
    degree: str | None
    degree_other: str | None
    work_authorisation: WorkAuthorisation | None
    threshold: int
    alert_frequency: AlertFrequency
    timezone: str


class PreferencesOut(PreferencesFields):
    """The stored Preferences, or the defaults with ``saved: false`` before the first save.
    ``role_titles``, ``locations`` and ``degree_other`` are untrusted text: render them as
    plain text."""

    saved: bool
    updated_at: datetime | None


class PreferencesIn(BaseModel):
    """The whole Preferences object (a full replace; every field is required, nullable ones
    may be null). Role titles and locations are trimmed, blanks dropped and duplicates
    (ignoring case) dropped; at most 10 of 80 characters each."""

    model_config = ConfigDict(extra="forbid")

    role_titles: PreferenceItems
    locations: PreferenceItems
    job_types: Annotated[list[JobType], Field(max_length=10), AfterValidator(_job_types)]
    batch_year: Annotated[int, Field(strict=True, ge=2000, le=2100)] | None
    # Strict: JSON numbers only (int or float), never booleans or strings.
    cgpa: Annotated[float, Field(strict=True, ge=0, le=10, allow_inf_nan=False)] | None
    degree: Annotated[str, AfterValidator(_degree_id)] | None
    degree_other: Annotated[
        str | None, BeforeValidator(_blank_to_none), AfterValidator(_plain_text)
    ]
    work_authorisation: WorkAuthorisation | None
    threshold: Annotated[int, Field(strict=True, ge=0, le=100)]
    alert_frequency: AlertFrequency
    timezone: Annotated[str, Field(max_length=64), AfterValidator(_timezone)]

    @field_validator("cgpa")
    @classmethod
    def _two_decimals(cls, value: float | None) -> float | None:
        # Stored as numeric(4, 2).
        return None if value is None else round(value, 2)

    @field_validator("degree_other")
    @classmethod
    def _only_with_other(cls, value: str | None, info: ValidationInfo) -> str | None:
        # ``degree`` is validated first (field order); when it failed it is absent here.
        if value is not None and "degree" in info.data and info.data["degree"] != "other":
            raise ValueError('must be null unless degree is "other"')
        return value


class DegreeOut(BaseModel):
    id: str
    label: str
