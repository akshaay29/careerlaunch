"""``SupabaseStorageFileStore``: the FileStore port on the Supabase Storage REST API.

* Authenticates with the service-role key (``apikey`` + ``Authorization: Bearer``), server
  side only. The key never appears in a URL or a log line.
* Objects live in one PRIVATE bucket (``STORAGE_BUCKET``); paths are server-chosen.
* Signed URLs carry Storage's ``download=<name>`` parameter, so Storage answers with
  ``Content-Disposition: attachment``.
* Vendor and transport errors become :class:`FileStoreError` (status code only, no body).
"""

import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.ports.file_store import FileStoreError

logger = logging.getLogger("app.adapters.supabase_storage")

DEFAULT_TIMEOUT_SECONDS = 30.0


def _quote_path(path: str) -> str:
    """URL-encode each segment of an object path (``/`` stays a separator)."""
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"invalid object path {path!r}")
    return "/".join(quote(segment, safe="") for segment in path.split("/"))


class SupabaseStorageFileStore:
    def __init__(
        self,
        storage_url: str,
        service_role_key: str,
        bucket: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not service_role_key:
            raise ValueError("a service-role key is required")
        self._base = storage_url.rstrip("/")
        self._key = service_role_key
        self._bucket = bucket
        self._transport = transport
        self._timeout = timeout

    # ------------------------------------------------------------------ plumbing

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            transport=self._transport,
            timeout=self._timeout,
            headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
            follow_redirects=False,
        )

    async def _request(self, op: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._client() as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("storage_request_failed", extra={"op": op, "exc_type": type(exc).__name__})
            raise FileStoreError(f"storage {op} failed: {type(exc).__name__}") from exc
        return response

    @staticmethod
    def _json_object(op: str, response: httpx.Response) -> dict[str, Any]:
        """The response body as a JSON object, else :class:`FileStoreError`."""
        try:
            body = response.json()
        except ValueError as exc:
            raise FileStoreError(f"storage {op} failed: invalid response") from exc
        if not isinstance(body, dict):
            raise FileStoreError(f"storage {op} failed: invalid response")
        return body

    @staticmethod
    def _fail(op: str, response: httpx.Response) -> FileStoreError:
        logger.warning("storage_error_status", extra={"op": op, "status": response.status_code})
        return FileStoreError(f"storage {op} failed: HTTP {response.status_code}")

    # ------------------------------------------------------------------ FileStore

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        url = f"/object/{quote(self._bucket, safe='')}/{_quote_path(path)}"
        response = await self._request(
            "put",
            "POST",
            url,
            content=data,
            # x-upsert: a retried upload of the same server-chosen path succeeds.
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
        if not response.is_success:
            raise self._fail("put", response)

    async def delete(self, paths: Sequence[str]) -> None:
        prefixes = list(paths)
        if not prefixes:
            return
        for p in prefixes:
            _quote_path(p)  # validate
        response = await self._request(
            "delete",
            "DELETE",
            f"/object/{quote(self._bucket, safe='')}",
            json={"prefixes": prefixes},
        )
        # Storage answers 200 with the objects it removed; missing ones are simply not listed.
        if response.status_code == 404 or response.is_success:
            return
        raise self._fail("delete", response)

    async def signed_download_url(
        self, path: str, expires_in_seconds: int, download_name: str
    ) -> str:
        if expires_in_seconds < 1:
            raise ValueError("expires_in_seconds must be positive")
        response = await self._request(
            "sign",
            "POST",
            f"/object/sign/{quote(self._bucket, safe='')}/{_quote_path(path)}",
            json={"expiresIn": expires_in_seconds},
        )
        if not response.is_success:
            raise self._fail("sign", response)
        body = self._json_object("sign", response)
        signed = body.get("signedURL") or body.get("signedUrl")
        if not isinstance(signed, str) or not signed:
            raise FileStoreError("storage sign failed: no signed URL")
        url = signed if signed.startswith(("http://", "https://")) else f"{self._base}/{signed.lstrip('/')}"
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}download={quote(download_name, safe='')}"

    # ------------------------------------------------------------------ bucket admin

    async def ensure_bucket(
        self, file_size_limit: int, allowed_mime_types: Sequence[str]
    ) -> str:
        """Create the bucket as PRIVATE if it is missing; make an existing one private, with
        the given size limit and allowed MIME types.

        Idempotent. Never makes a bucket public. Returns ``created``, ``updated`` or
        ``unchanged``.
        """
        bucket = quote(self._bucket, safe="")
        mime_types = sorted(allowed_mime_types)
        settings: dict[str, Any] = {
            "public": False,
            "file_size_limit": file_size_limit,
            "allowed_mime_types": mime_types,
        }
        response = await self._request("bucket", "GET", f"/bucket/{bucket}")
        if response.is_success:
            current = self._json_object("bucket lookup", response)
            current_types = current.get("allowed_mime_types")
            if (
                current.get("public") is False
                and current.get("file_size_limit") == file_size_limit
                and isinstance(current_types, list)
                and sorted(current_types) == mime_types
            ):
                return "unchanged"
            update = await self._request("bucket", "PUT", f"/bucket/{bucket}", json=settings)
            if not update.is_success:
                raise self._fail("bucket update", update)
            return "updated"
        if response.status_code not in (400, 404):
            raise self._fail("bucket lookup", response)
        create = await self._request(
            "bucket", "POST", "/bucket", json={"id": self._bucket, "name": self._bucket, **settings}
        )
        if not create.is_success:
            raise self._fail("bucket create", create)
        return "created"


def from_settings(settings: Settings | None = None) -> SupabaseStorageFileStore:
    """The production store, from ``SUPABASE_URL``, ``SUPABASE_SERVICE_ROLE_KEY`` and
    ``STORAGE_BUCKET``. Raises ``RuntimeError`` when the key is not configured."""
    settings = settings or get_settings()
    if settings.supabase_service_role_key is None:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required to use Supabase Storage")
    return SupabaseStorageFileStore(
        settings.supabase_storage_url,
        settings.supabase_service_role_key.get_secret_value(),
        settings.storage_bucket,
    )
