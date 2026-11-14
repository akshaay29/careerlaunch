"""``SupabaseAuthAdmin``: the AuthAdmin port on the Supabase Auth admin REST API.

* ``DELETE <SUPABASE_URL>/auth/v1/admin/users/{id}`` with the service-role key (``apikey`` +
  ``Authorization: Bearer``), server side only. The key never appears in a URL or a log line.
* A 404 means the user is already gone: success (the purge job is idempotent).
* Vendor and transport errors become :class:`AuthAdminError` (status code only, no body).
"""

import logging
import uuid

import httpx

from app.core.config import Settings, get_settings
from app.ports.auth_admin import AuthAdminError

logger = logging.getLogger("app.adapters.supabase_auth")

DEFAULT_TIMEOUT_SECONDS = 30.0


class SupabaseAuthAdmin:
    def __init__(
        self,
        auth_url: str,
        service_role_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not service_role_key:
            raise ValueError("a service-role key is required")
        self._base = auth_url.rstrip("/")
        self._key = service_role_key
        self._transport = transport
        self._timeout = timeout

    async def delete_user(self, user_id: uuid.UUID) -> None:
        if not isinstance(user_id, uuid.UUID):
            raise ValueError("user_id must be a uuid.UUID")
        try:
            async with httpx.AsyncClient(
                base_url=self._base,
                transport=self._transport,
                timeout=self._timeout,
                headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
                follow_redirects=False,
            ) as client:
                response = await client.delete(f"/admin/users/{user_id}")
        except httpx.HTTPError as exc:
            logger.warning("auth_admin_request_failed", extra={"exc_type": type(exc).__name__})
            raise AuthAdminError(f"auth delete_user failed: {type(exc).__name__}") from exc
        if response.is_success or response.status_code == 404:
            return
        logger.warning("auth_admin_error_status", extra={"status": response.status_code})
        raise AuthAdminError(f"auth delete_user failed: HTTP {response.status_code}")


def from_settings(settings: Settings | None = None) -> SupabaseAuthAdmin:
    """The production adapter, from ``SUPABASE_URL`` and ``SUPABASE_SERVICE_ROLE_KEY``.
    Raises ``RuntimeError`` when the key is not configured."""
    settings = settings or get_settings()
    if settings.supabase_service_role_key is None:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required to use the Auth admin API")
    return SupabaseAuthAdmin(
        f"{settings.supabase_url.rstrip('/')}/auth/v1",
        settings.supabase_service_role_key.get_secret_value(),
    )
