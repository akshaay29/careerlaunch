"""In-memory ProviderRunner fake (AD-3). Tests never call real Apify."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import SecretStr

from app.ports.provider_runner import ProviderAccount, ProviderKeyInvalid, ProviderUnavailable


@dataclass
class FakeProviderRunner:
    """``mode``: ``valid`` (returns ``ProviderAccount(user_id)``), ``invalid`` (raises
    :class:`ProviderKeyInvalid`) or ``unavailable`` (raises :class:`ProviderUnavailable`).
    ``validated`` records every token value it was asked about."""

    mode: Literal["valid", "invalid", "unavailable"] = "valid"
    user_id: str | None = "apify-user-1"
    validated: list[str] = field(default_factory=list)

    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        self.validated.append(token.get_secret_value())
        if self.mode == "invalid":
            raise ProviderKeyInvalid("fake: token rejected")
        if self.mode == "unavailable":
            raise ProviderUnavailable("fake: provider down")
        return ProviderAccount(user_id=self.user_id)
