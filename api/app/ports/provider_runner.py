"""ProviderRunner port (AD-3, AD-12): the Student's Apify account.

Implemented by ``app.adapters.apify`` (Apify REST API over httpx) and by an in-memory fake in
``tests/fakes/``. Installed process-wide by app wiring (``app.main``) with
:func:`install_provider_runner`; routers reach it through :func:`provider_runner_dependency`,
so ``modules`` never import ``adapters``.

Story 2.2 added token validation. Story 2.3 adds the run methods (start a run, read its
state, fetch its items); ``listings.service`` (``start_provider_run`` and the poll of the run
it started) is their only caller. Story 2.4 adds ``ProviderRejected.kind`` and
``billing_cycle_end``. Every method takes the token as a ``SecretStr``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import Request
from pydantic import SecretStr


class ProviderError(Exception):
    """Base for ProviderRunner errors. Messages never contain the token."""


class ProviderKeyInvalid(ProviderError):
    """The provider rejected the token (wrong, revoked or expired)."""


class ProviderUnavailable(ProviderError):
    """The provider could not answer: network error, timeout, 5xx or 429."""


RejectionKind = Literal["credit", "permission", "blocked"]


class ProviderRejected(ProviderError):
    """The provider refused the request for the account's credit or plan (HTTP 402 or 403
    on a run call). ``kind`` says why, and ``listings`` pauses the Student accordingly:

    * ``credit``: the account's credit or usage limit is used up (402, or a usage/credit
      error type);
    * ``permission``: the token may not do this (403 ``insufficient-permissions``);
    * ``blocked``: any other refusal (e.g. a blocked or disabled account).
    """

    def __init__(self, message: str = "", *, kind: RejectionKind = "blocked") -> None:
        super().__init__(message)
        self.kind: RejectionKind = kind


RunStatus = Literal["READY", "RUNNING", "SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"]
TERMINAL_FAILURES: frozenset[str] = frozenset({"FAILED", "TIMED-OUT", "ABORTED"})


@dataclass(frozen=True)
class RunState:
    """A provider run as last seen. ``usage_usd`` is the cost so far (None when unknown);
    ``dataset_id`` holds the run's items."""

    status: RunStatus
    usage_usd: float | None
    dataset_id: str | None


@dataclass(frozen=True)
class ProviderAccount:
    """The provider account a token belongs to. ``user_id`` is None when the token is
    accepted but not allowed to read the account id (a scoped token)."""

    user_id: str | None


@runtime_checkable
class ProviderRunner(Protocol):
    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        """Check ``token`` with the provider. Raises :class:`ProviderKeyInvalid` when the
        provider rejects it and :class:`ProviderUnavailable` when it cannot answer."""
        ...

    async def start_run(
        self,
        token: SecretStr,
        actor: str,
        run_input: dict[str, Any],
        *,
        max_items: int,
        max_charge_usd: float,
    ) -> str:
        """Start ``actor`` (``owner/name``) asynchronously with ``run_input`` and the caps;
        returns the provider run id. Never waits for the run."""
        ...

    async def get_run(self, token: SecretStr, run_id: str) -> RunState:
        """The run's current status, cost and dataset."""
        ...

    async def fetch_items(
        self, token: SecretStr, dataset_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        """At most ``limit`` items of the dataset (raw provider output: map, never store)."""
        ...

    async def abort_run(self, token: SecretStr, run_id: str) -> None:
        """Ask the provider to abort the run so it stops billing. Best effort and idempotent:
        never raises for a provider error (it is logged)."""
        ...

    async def billing_cycle_end(self, token: SecretStr) -> datetime | None:
        """When the account's current monthly usage cycle ends (UTC), or None when unknown.
        Never raises. Callers hold no database transaction."""
        ...


class LazyProviderRunner:
    """Builds the real adapter on first use, so creating the app needs no configuration."""

    def __init__(self, factory: Callable[[], ProviderRunner]) -> None:
        self._factory = factory
        self._runner: ProviderRunner | None = None

    def _get(self) -> ProviderRunner:
        if self._runner is None:
            self._runner = self._factory()
        return self._runner

    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        return await self._get().validate_token(token)

    async def start_run(
        self,
        token: SecretStr,
        actor: str,
        run_input: dict[str, Any],
        *,
        max_items: int,
        max_charge_usd: float,
    ) -> str:
        return await self._get().start_run(
            token, actor, run_input, max_items=max_items, max_charge_usd=max_charge_usd
        )

    async def get_run(self, token: SecretStr, run_id: str) -> RunState:
        return await self._get().get_run(token, run_id)

    async def fetch_items(
        self, token: SecretStr, dataset_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        return await self._get().fetch_items(token, dataset_id, limit=limit)

    async def abort_run(self, token: SecretStr, run_id: str) -> None:
        await self._get().abort_run(token, run_id)

    async def billing_cycle_end(self, token: SecretStr) -> datetime | None:
        return await self._get().billing_cycle_end(token)


_installed: ProviderRunner | None = None


def install_provider_runner(runner: ProviderRunner) -> None:
    """Make ``runner`` the process-wide ProviderRunner (used by tasks run by the drain)."""
    global _installed
    _installed = runner


def get_provider_runner() -> ProviderRunner:
    if _installed is None:
        raise RuntimeError(
            "No ProviderRunner is installed (app wiring calls install_provider_runner)"
        )
    return _installed


def provider_runner_dependency(request: Request) -> ProviderRunner:
    """FastAPI dependency: the runner wired into this app (``app.state.provider_runner``)."""
    runner = getattr(request.app.state, "provider_runner", None)
    return runner if runner is not None else get_provider_runner()
