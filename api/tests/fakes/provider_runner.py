"""In-memory ProviderRunner fake (AD-3). Tests never call real Apify."""

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import SecretStr

from app.ports.provider_runner import (
    ProviderAccount,
    ProviderError,
    ProviderKeyInvalid,
    ProviderUnavailable,
    RunState,
)


@dataclass(frozen=True)
class StartCall:
    token: str
    actor: str
    run_input: dict[str, Any]
    max_items: int
    max_charge_usd: float


@dataclass
class FakeProviderRunner:
    """``mode``: ``valid`` (returns ``ProviderAccount(user_id)``), ``invalid`` (raises
    :class:`ProviderKeyInvalid`) or ``unavailable`` (raises :class:`ProviderUnavailable`).
    ``validated`` records every token value it was asked about.

    Runs: ``start_run`` records a :class:`StartCall` in ``started`` and returns ``run-<n>``
    (or raises ``start_error``). ``get_run`` pops the next state from ``states`` (the last
    one repeats; default SUCCEEDED with dataset ``ds-<n>``), or raises ``get_error``.
    ``fetch_items`` returns ``datasets[dataset_id]`` up to ``limit`` (or raises
    ``fetch_error``). ``abort_run`` records ``(token, run_id)`` in ``aborted``."""

    mode: Literal["valid", "invalid", "unavailable"] = "valid"
    user_id: str | None = "apify-user-1"
    validated: list[str] = field(default_factory=list)

    started: list[StartCall] = field(default_factory=list)
    start_error: ProviderError | None = None
    states: deque[RunState] = field(default_factory=deque)
    get_error: ProviderError | None = None
    gets: list[tuple[str, str]] = field(default_factory=list)
    datasets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    fetches: list[tuple[str, str, int]] = field(default_factory=list)
    fetch_error: ProviderError | None = None
    aborted: list[tuple[str, str]] = field(default_factory=list)

    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        self.validated.append(token.get_secret_value())
        if self.mode == "invalid":
            raise ProviderKeyInvalid("fake: token rejected")
        if self.mode == "unavailable":
            raise ProviderUnavailable("fake: provider down")
        return ProviderAccount(user_id=self.user_id)

    async def start_run(
        self,
        token: SecretStr,
        actor: str,
        run_input: dict[str, Any],
        *,
        max_items: int,
        max_charge_usd: float,
    ) -> str:
        assert isinstance(token, SecretStr)
        self.started.append(
            StartCall(token.get_secret_value(), actor, run_input, max_items, max_charge_usd)
        )
        if self.start_error is not None:
            raise self.start_error
        return f"run{len(self.started)}"

    def queue(self, *states: RunState) -> None:
        self.states.extend(states)

    async def get_run(self, token: SecretStr, run_id: str) -> RunState:
        assert isinstance(token, SecretStr)
        self.gets.append((token.get_secret_value(), run_id))
        if self.get_error is not None:
            raise self.get_error
        if len(self.states) > 1:
            return self.states.popleft()
        if self.states:
            return self.states[0]
        return RunState("SUCCEEDED", 0.01, f"ds{run_id}")

    async def fetch_items(
        self, token: SecretStr, dataset_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        assert isinstance(token, SecretStr)
        self.fetches.append((token.get_secret_value(), dataset_id, limit))
        if self.fetch_error is not None:
            raise self.fetch_error
        return list(self.datasets.get(dataset_id, []))[:limit]

    async def abort_run(self, token: SecretStr, run_id: str) -> None:
        assert isinstance(token, SecretStr)
        self.aborted.append((token.get_secret_value(), run_id))
