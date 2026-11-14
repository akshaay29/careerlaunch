"""``ApifyProviderRunner``: the ProviderRunner port on the Apify REST API (AD-12).

Token validation is ``GET <APIFY_API_BASE>/v2/users/me`` with ``Authorization: Bearer
<token>`` and ``x-apify-integration-platform: <APIFY_INTEGRATION_PLATFORM>``. The token is
only ever in that header: never in a URL, a log line, an exception message or a ``repr``.

Response mapping:

* 200 with a JSON object whose ``data`` is an object: valid; ``data.id`` is the provider
  user id when it is a non-empty string (else None). Any other 200 body: unavailable.
* 401: :class:`ProviderKeyInvalid` (Apify's ``invalid-token``: wrong, revoked or expired).
* 403 with error type ``insufficient-permissions``: valid. Apify authenticated the token but
  its scope does not cover reading the account (a scoped token with Run permission on
  approved actors only may be refused here); ``user_id`` is None.
* any other 403: :class:`ProviderKeyInvalid` (e.g. a blocked or disabled account).
* 429, 5xx, timeouts, transport errors and an unusable base URL (``InvalidURL``,
  ``UnsupportedProtocol``): :class:`ProviderUnavailable`.
* anything else (other 4xx, a 200 that is not JSON): :class:`ProviderUnavailable`, since
  it says nothing reliable about the token.
"""

import logging

import httpx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.ports.provider_runner import ProviderAccount, ProviderKeyInvalid, ProviderUnavailable

logger = logging.getLogger("app.adapters.apify")

DEFAULT_TIMEOUT_SECONDS = 15.0
INTEGRATION_HEADER = "x-apify-integration-platform"
INSUFFICIENT_PERMISSIONS = "insufficient-permissions"


def _error_type(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    value = error.get("type") if isinstance(error, dict) else None
    return value if isinstance(value, str) else None


class ApifyProviderRunner:
    def __init__(
        self,
        api_base: str,
        integration_platform: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base = api_base.rstrip("/")
        self._platform = integration_platform
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"ApifyProviderRunner(api_base={self._base!r})"

    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        if not isinstance(token, SecretStr):
            raise TypeError("token must be a SecretStr")
        try:
            async with httpx.AsyncClient(
                base_url=self._base,
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    "/v2/users/me",
                    headers={
                        "Authorization": f"Bearer {token.get_secret_value()}",
                        INTEGRATION_HEADER: self._platform,
                        "Accept": "application/json",
                    },
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # HTTPError covers timeouts, transport errors and UnsupportedProtocol; a malformed
            # base URL raises InvalidURL, which is not an HTTPError.
            # Only the exception type: an httpx message can echo the request.
            logger.warning("apify_request_failed", extra={"exc_type": type(exc).__name__})
            raise ProviderUnavailable(f"Apify request failed: {type(exc).__name__}") from None

        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError:
                logger.warning("apify_bad_response", extra={"status": status})
                raise ProviderUnavailable("Apify returned an unreadable response") from None
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict):
                # Not Apify's user payload: nothing proves the token was checked.
                logger.warning("apify_bad_response", extra={"status": status})
                raise ProviderUnavailable("Apify returned an unexpected response")
            user_id = data.get("id")
            return ProviderAccount(
                user_id=user_id if isinstance(user_id, str) and user_id.strip() else None
            )
        if status == 401:
            raise ProviderKeyInvalid("Apify rejected the token (401)")
        if status == 403:
            if _error_type(response) == INSUFFICIENT_PERMISSIONS:
                return ProviderAccount(user_id=None)
            raise ProviderKeyInvalid("Apify refused the token (403)")
        logger.warning("apify_error_status", extra={"status": status})
        raise ProviderUnavailable(f"Apify returned HTTP {status}")


def from_settings(settings: Settings | None = None) -> ApifyProviderRunner:
    """The production adapter, from ``APIFY_API_BASE`` and ``APIFY_INTEGRATION_PLATFORM``."""
    settings = settings or get_settings()
    return ApifyProviderRunner(settings.apify_api_base, settings.apify_integration_platform)
