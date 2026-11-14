"""FileStore port (AD-3, Files convention): private object storage for Student files.

Paths are chosen by the server only (``<owner_id>/<kind>/<uuid>``), never by a client.
Implemented by ``app.adapters.supabase_storage`` (Supabase Storage REST API) and by an
in-memory fake in ``tests/fakes/``.

The process-wide store is installed by app wiring (``app.main``) with :func:`install_file_store`
and reached by routers through :func:`file_store_dependency` and by tasks through
:func:`get_file_store`, so ``modules`` never import ``adapters``.
"""

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from fastapi import Request


class FileStoreError(Exception):
    """The store could not complete an operation (adapters translate vendor errors to this)."""


@runtime_checkable
class FileStore(Protocol):
    async def put(self, path: str, data: bytes, content_type: str) -> None:
        """Store ``data`` at ``path`` (overwriting an object already there)."""
        ...

    async def delete(self, paths: Sequence[str]) -> None:
        """Delete every object in ``paths``. Idempotent: a missing object is not an error."""
        ...

    async def signed_download_url(
        self, path: str, expires_in_seconds: int, download_name: str
    ) -> str:
        """A short-lived URL that downloads ``path`` as an attachment named ``download_name``."""
        ...


class LazyFileStore:
    """Builds the real store on first use, so creating the app (OpenAPI export, tests) never
    needs storage credentials."""

    def __init__(self, factory: Callable[[], FileStore]) -> None:
        self._factory = factory
        self._store: FileStore | None = None

    def _get(self) -> FileStore:
        if self._store is None:
            self._store = self._factory()
        return self._store

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        await self._get().put(path, data, content_type)

    async def delete(self, paths: Sequence[str]) -> None:
        await self._get().delete(paths)

    async def signed_download_url(
        self, path: str, expires_in_seconds: int, download_name: str
    ) -> str:
        return await self._get().signed_download_url(path, expires_in_seconds, download_name)


_installed: FileStore | None = None


def install_file_store(store: FileStore) -> None:
    """Make ``store`` the process-wide FileStore (used by tasks run by the drain)."""
    global _installed
    _installed = store


def get_file_store() -> FileStore:
    if _installed is None:
        raise RuntimeError("No FileStore is installed (app wiring calls install_file_store)")
    return _installed


def file_store_dependency(request: Request) -> FileStore:
    """FastAPI dependency: the store wired into this app (``app.state.file_store``)."""
    store = getattr(request.app.state, "file_store", None)
    return store if store is not None else get_file_store()
