"""accounts.schemas: API DTOs."""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_core import PydanticCustomError

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


TOKEN_MIN_LENGTH = 8
TOKEN_MAX_LENGTH = 200
# Printable ASCII without spaces: what an API token is made of (and what an HTTP header can
# carry). Smart quotes, NBSP, control characters and inner whitespace are refused up front.
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")
TOKEN_FORMAT_MESSAGE = (
    "This doesn't look like an Apify token: it must be 8 to 200 characters with no spaces "
    "or special characters. Copy it again from the Apify console."
)


class ProviderKeyInput(BaseModel):
    """A Provider Key to validate and store. Trimmed, then 8 to 200 printable ASCII
    characters with no whitespace. Write-only: it is never returned, logged or echoed in a
    validation error."""

    token: SecretStr = Field(min_length=TOKEN_MIN_LENGTH, max_length=TOKEN_MAX_LENGTH)

    @field_validator("token", mode="before")
    @classmethod
    def _trim_and_check(cls, v: object) -> object:
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if not isinstance(v, str):
            return v  # the SecretStr type check reports it
        v = v.strip()
        if not (
            TOKEN_MIN_LENGTH <= len(v) <= TOKEN_MAX_LENGTH and _TOKEN_RE.fullmatch(v)
        ):
            raise PydanticCustomError("token_format", TOKEN_FORMAT_MESSAGE)
        return v


class ProviderKeyMeta(BaseModel):
    """What the Student sees about their Provider Key: whether one is stored, its last four
    characters and when it was last validated. Never the token."""

    provider: Literal["apify"]
    has_key: bool
    last4: str | None
    validated_at: datetime | None
    # True when the stored key can no longer be read (its master-key version was retired):
    # the Student must enter it again.
    needs_reentry: bool
