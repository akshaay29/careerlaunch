"""profile.models: ``resumes`` (DDL in 0006_resumes), ``skill_profiles`` (0007) and
``preferences`` (0008)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Numeric, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Resume(Base):
    """One uploaded Resume version. ``id`` is also the Storage object id
    (``<owner_id>/resume/<id>``). At most one ``is_active`` row per owner."""

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # FK to accounts(id) ON DELETE CASCADE is declared in the migration (AD-2: no ORM link
    # into another module's table).
    owner_id: Mapped[uuid.UUID] = mapped_column()
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SkillProfile(Base):
    """One Skill Profile per owner. Effective skills are
    ``(extracted_skills | added_skills) - removed_skills``; ``custom_skills`` (terms outside
    the vocabulary) are stored and shown but never scored. ``status`` is None when the row
    exists only because of manual edits."""

    __tablename__ = "skill_profiles"

    # FKs (accounts CASCADE, resumes SET NULL) are declared in the migration.
    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(Text)
    extracted_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    vocab_version: Mapped[str | None] = mapped_column(Text)
    added_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    removed_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    custom_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    education: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    experience: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Preferences(Base):
    """One Preferences row per owner (FR-5). No row means the defaults. NULL eligibility
    fields mean "not stated" (Unknown eligibility in Epic 3)."""

    __tablename__ = "preferences"

    # FK to accounts(id) ON DELETE CASCADE is declared in the migration.
    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role_titles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    job_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    batch_year: Mapped[int | None] = mapped_column(Integer)
    cgpa: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    degree: Mapped[str | None] = mapped_column(Text)
    degree_other: Mapped[str | None] = mapped_column(Text)
    work_authorisation: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[int] = mapped_column(SmallInteger, server_default="60")
    alert_frequency: Mapped[str] = mapped_column(Text, server_default="daily")
    timezone: Mapped[str] = mapped_column(Text, server_default="Asia/Kolkata")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
