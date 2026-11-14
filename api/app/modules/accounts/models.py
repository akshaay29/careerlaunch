"""accounts.models: ``accounts`` (DDL in migration 0004_accounts), ``consents`` (0005),
``deleted_accounts`` (0010) and ``provider_credentials`` (0012)."""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, LargeBinary, SmallInteger, Text, func
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


class ProviderCredential(Base):
    """A Student's Provider Key (DDL in migration 0012_provider_credentials).

    ``ciphertext`` is ``core.crypto`` output with AAD ``provider_credentials:<id>:<owner_id>``;
    the plaintext token is never stored. ``last4`` is the only plaintext fragment kept. The
    ``__repr__`` never shows the ciphertext.
    """

    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(SmallInteger)
    last4: Mapped[str] = mapped_column(CHAR(4))
    provider_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"ProviderCredential(id={self.id!s}, provider={self.provider!r})"


class ProviderStatus(Base):
    """A Student's collection status across every provider purpose (DDL in migration
    0013_provider_runs). No row means ``active``. Story 2.4 writes it."""

    __tablename__ = "provider_status"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(Text, server_default="active")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exhausted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
