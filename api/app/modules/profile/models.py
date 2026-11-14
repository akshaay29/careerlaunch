"""profile.models: ``resumes`` (DDL in migration 0006_resumes)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
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
