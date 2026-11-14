"""listings.models: ``sources`` and ``listings`` (DDL in migration 0011_listings).

Both are global tables with no ``owner_id`` (AD-5). Foreign keys are declared in the
migration (AD-2: no ORM link into another module's table).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.core.db import Base


class Source(Base):
    """A Source of Listings. ``enabled`` gates collection for it."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Listing(Base):
    """One Listing, unique on ``(source, source_job_id)``. ``visibility_owner_id`` NULL
    means the shared pool. No column holds the description (AD-7)."""

    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    source: Mapped[str] = mapped_column(Text)
    source_job_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text, server_default="")
    location: Mapped[str] = mapped_column(Text, server_default="")
    job_type: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[date | None] = mapped_column(Date)
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    vocab_version: Mapped[str] = mapped_column(Text)
    # Lexemes only; never loaded for browsing.
    search_doc: Mapped[str] = deferred(mapped_column(TSVECTOR))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_by: Mapped[uuid.UUID | None] = mapped_column()
    visibility_owner_id: Mapped[uuid.UUID | None] = mapped_column()
    pin_count: Mapped[int] = mapped_column(Integer, server_default="0")
    relevance: Mapped[float] = mapped_column(Float, server_default="0")
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
