"""accounts.schemas: API DTOs."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.accounts.consent_text import ConsentPurpose


class Me(BaseModel):
    """The calling account. ``role`` comes from the database, never from the token."""

    id: uuid.UUID
    email: str
    role: Literal["student", "admin"]


class ConsentStatus(BaseModel):
    """One purpose: the text to show (plain-text paragraphs) and the Student's status on it.

    ``status``: ``granted`` (active on the current version), ``outdated`` (active on an older
    version: agree again), ``revoked`` (withdrawn) or ``none`` (never agreed).
    """

    purpose: ConsentPurpose
    title: str
    body: list[str]
    version: str
    status: Literal["granted", "revoked", "none", "outdated"]
    granted_at: datetime | None
    revoked_at: datetime | None


class RightsNotice(BaseModel):
    """The India DPDP Act rights notice, shown with the consent texts (plain text)."""

    title: str
    body: list[str]


class ConsentOverview(BaseModel):
    purposes: list[ConsentStatus]
    rights: RightsNotice
    notice_version: str


class ConsentGrant(BaseModel):
    """The version of the text the Student was shown and agreed to."""

    version: str = Field(min_length=1, max_length=32)


class AccountDeletion(BaseModel):
    """Confirms an account deletion: the account's email, typed by the Student. Compared
    after trimming and ignoring case."""

    confirm_email: str = Field(max_length=320)
