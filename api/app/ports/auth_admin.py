"""AuthAdmin port (AD-3, AD-17): privileged operations on Supabase Auth users.

Implemented by ``app.adapters.supabase_auth`` (Auth admin REST API) and by an in-memory fake
in ``tests/fakes/``. Installed process-wide by app wiring (``app.main``) with
:func:`install_auth_admin` and reached by tasks through :func:`get_auth_admin`, so
``modules`` never import ``adapters``.
"""

import uuid
from collections.abc import Callable
from typing import Protocol, runtime_checkable


class AuthAdminError(Exception):
    """The Auth admin API could not complete an operation (adapters translate vendor errors)."""


@runtime_checkable
class AuthAdmin(Protocol):
    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Delete the Auth user ``user_id``. Idempotent: a user that does not exist (already
        deleted) is a success. Raises :class:`AuthAdminError` on any other failure."""
        ...


class LazyAuthAdmin:
    """Builds the real adapter on first use, so creating the app (OpenAPI export, tests)
    never needs the service-role key."""

    def __init__(self, factory: Callable[[], AuthAdmin]) -> None:
        self._factory = factory
        self._admin: AuthAdmin | None = None

    def _get(self) -> AuthAdmin:
        if self._admin is None:
            self._admin = self._factory()
        return self._admin

    async def delete_user(self, user_id: uuid.UUID) -> None:
        await self._get().delete_user(user_id)


_installed: AuthAdmin | None = None


def install_auth_admin(admin: AuthAdmin) -> None:
    """Make ``admin`` the process-wide AuthAdmin (used by tasks run by the drain)."""
    global _installed
    _installed = admin


def get_auth_admin() -> AuthAdmin:
    if _installed is None:
        raise RuntimeError("No AuthAdmin is installed (app wiring calls install_auth_admin)")
    return _installed
