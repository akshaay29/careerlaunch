"""accounts.models: the ``accounts`` table (DDL lives in migration 0004_accounts)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ROLES = ("student", "admin")


class Account(Base):
    """One row per Supabase Auth user. ``id`` IS the Auth uid (not a UUID v7)."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text, server_default="student")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
