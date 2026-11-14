"""ProviderRunner port (AD-3, AD-12): the Student's Apify account.

Implemented by ``app.adapters.apify`` (Apify REST API over httpx) and by an in-memory fake in
``tests/fakes/``. Installed process-wide by app wiring (``app.main``) with
:func:`install_provider_runner`; routers reach it through :func:`provider_runner_dependency`,
so ``modules`` never import ``adapters``.

Story 2.2 needs only token validation. Story 2.3 adds the run methods (start a run, poll it,
fetch its items) to this protocol; ``listings.service.start_provider_run`` stays their only
caller.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fastapi import Request
from pydantic import SecretStr


class ProviderError(Exception):
    """Base for ProviderRunner errors. Messages never contain the token."""


class ProviderKeyInvalid(ProviderError):
    """The provider rejected the token (wrong, revoked or expired)."""


class ProviderUnavailable(ProviderError):
    """The provider could not answer: network error, timeout, 5xx or 429."""


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
