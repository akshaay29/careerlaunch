"""tracker.models: ``applications`` and ``application_status_history`` (DDL in migration
0018_applications).

Foreign keys are declared in the migration (AD-2: no ORM link into another module's table).
``status`` is written only by ``tracker.service.transition`` (AD-10 style single writer).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Application(Base):
    """One saved Listing of one Student (FR-16). ``listing_id`` is NULL once the Listing is
    gone; ``title``, ``company``, ``url`` and ``deadline`` are the snapshot taken at save."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column()
    listing_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    applied_on: Mapped[date | None] = mapped_column(Date)
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text, server_default="")
    url: Mapped[str] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApplicationStatusHistory(Base):
    """One Status change. ``from_status`` is NULL for the row written at save."""

    __tablename__ = "application_status_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    application_id: Mapped[uuid.UUID] = mapped_column()
    owner_id: Mapped[uuid.UUID] = mapped_column()
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
