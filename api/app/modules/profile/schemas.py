"""profile.schemas: API DTOs."""

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.profile.files import ResumeContentType

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
