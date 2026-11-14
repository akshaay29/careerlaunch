"""accounts.models: ``accounts`` (DDL in migration 0004_accounts), ``consents`` (0005) and
``deleted_accounts`` (0010)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
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


class Consent(Base):
    """One grant of consent for one purpose (DDL in migration 0005_consents).

    Granting inserts a row; revoking sets ``revoked_at``. At most one row per
    ``(owner_id, purpose)`` has ``revoked_at IS NULL`` (partial unique index).
    """

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'revoked' (withdrawn by the Student) or 'superseded' (newer version granted); set with
    # revoked_at.
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeletedAccount(Base):
    """Tombstone of a deleted account (DDL in migration 0010_deleted_accounts).

    Global, no foreign key, and nothing but the Auth uid and the time: no email or other
    personal data. Stops a still-valid access token from re-provisioning the account.
    """

    __tablename__ = "deleted_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set by the purge jobs when they succeed; NULL means that part is still pending.
    files_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auth_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
