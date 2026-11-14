"""listings.models: ``sources`` and ``listings`` (DDL in migration 0011_listings),
``provider_runs`` (0013_provider_runs) and ``listing_blocks`` (0015_listing_maintenance).

``sources``, ``listings`` and ``listing_blocks`` are global tables with no ``owner_id`` (AD-5).
Foreign keys are declared in the migration (AD-2: no ORM link into another module's table).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
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


class ProviderRun(Base):
    """One attempted provider run (AD-12), written only by ``listings.service``.
    ``owner_id`` NULL is the System seed run. Ids, caps, counts and cost only: never the
    token or a raw item."""

    __tablename__ = "provider_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    owner_id: Mapped[uuid.UUID | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(Text)
    run_key: Mapped[str] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    apify_run_id: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    max_items: Mapped[int] = mapped_column(Integer)
    max_charge_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    # max_items x APIFY_PRICE_PER_ITEM_USD (0014; NULL on older rows).
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    items: Mapped[int | None] = mapped_column(Integer)
    ingested: Mapped[int | None] = mapped_column(Integer)
    rejected: Mapped[int | None] = mapped_column(Integer)
    duplicates: Mapped[int | None] = mapped_column(Integer)
    not_visible: Mapped[int | None] = mapped_column(Integer)
    error_kind: Mapped[str | None] = mapped_column(Text)
    poll_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingBlock(Base):
    """One takedown (Story 2.5): a Listing (``source`` + ``value`` = ``source_job_id``), a URL
    (canonical form) or a company (trimmed, case-folded) that is never ingested again."""

    __tablename__ = "listing_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    kind: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
