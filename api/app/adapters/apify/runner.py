"""``ApifyProviderRunner``: the ProviderRunner port on the Apify REST API (AD-12).

Every request sends ``Authorization: Bearer <token>`` and ``x-apify-integration-platform:
<APIFY_INTEGRATION_PLATFORM>``. The token is only ever in that header: never in a URL, a log
line, an exception message or a ``repr``.

Token validation is ``GET <APIFY_API_BASE>/v2/users/me``. Response mapping:

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

Runs (Story 2.3), same headers and the same transport mapping:

* ``start_run``: ``POST /v2/acts/<owner>~<name>/runs?maxItems=<n>&maxTotalChargeUsd=<usd>``
  with the input as the JSON body; returns ``data.id``. The run is asynchronous: the call
  never waits for it to finish.
* ``get_run``: ``GET /v2/actor-runs/<id>``: ``data.status``, ``data.usageTotalUsd`` and
  ``data.defaultDatasetId``. The transitional ``TIMING-OUT`` and ``ABORTING`` read as
  ``RUNNING`` (poll again).
* ``fetch_items``: ``GET /v2/datasets/<id>/items?format=json&clean=true&limit=<n>``.
* ``abort_run``: ``POST /v2/actor-runs/<id>/abort``; best effort: any error is logged (type
  and status only) and swallowed, and aborting a finished run is harmless.
* Status mapping for run calls: 401 :class:`ProviderKeyInvalid`; 402 and 403
  :class:`ProviderRejected` (credit, plan or permission); 429, 5xx, other 4xx and unusable
  bodies :class:`ProviderUnavailable`.
"""

import logging
import math
import re
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.config import ACTOR_RE, Settings, get_settings
from app.ports.provider_runner import (
    ProviderAccount,
    ProviderKeyInvalid,
    ProviderRejected,
    ProviderUnavailable,
    RunState,
    RunStatus,
)

logger = logging.getLogger("app.adapters.apify")

DEFAULT_TIMEOUT_SECONDS = 15.0
INTEGRATION_HEADER = "x-apify-integration-platform"
INSUFFICIENT_PERMISSIONS = "insufficient-permissions"
_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
_STATUSES: dict[str, RunStatus] = {
    "READY": "READY",
    "RUNNING": "RUNNING",
    "TIMING-OUT": "RUNNING",
    "ABORTING": "RUNNING",
    "SUCCEEDED": "SUCCEEDED",
    "FAILED": "FAILED",
    "TIMED-OUT": "TIMED-OUT",
    "ABORTED": "ABORTED",
}


def _error_type(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    value = error.get("type") if isinstance(error, dict) else None
    return value if isinstance(value, str) else None


def _data(body: Any, call: str) -> dict[str, Any]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        logger.warning("apify_bad_response", extra={"call": call})
        raise ProviderUnavailable("Apify returned an unexpected response")
    return data


def _check_id(value: str, what: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{what} is not an Apify id")


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

    async def _send(
        self,
        method: str,
        path: str,
        token: SecretStr,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One request with the bearer token and the integration header. Transport failures
        are :class:`ProviderUnavailable`; mapping the status is the caller's job."""
        if not isinstance(token, SecretStr):
            raise TypeError("token must be a SecretStr")
        try:
            async with httpx.AsyncClient(
                base_url=self._base,
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                return await client.request(
                    method,
                    path,
                    params=params,
                    json=json,
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

    async def validate_token(self, token: SecretStr) -> ProviderAccount:
        response = await self._send("GET", "/v2/users/me", token)
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

    # ----------------------------------------------------------------------- runs

    async def _run_call(
        self,
        method: str,
        path: str,
        token: SecretStr,
        *,
        ok: tuple[int, ...] = (200,),
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """A run call: the parsed JSON body on an ``ok`` status, else the run-call mapping."""
        response = await self._send(method, path, token, params=params, json=json)
        status = response.status_code
        if status in ok:
            try:
                return response.json()
            except ValueError:
                logger.warning("apify_bad_response", extra={"status": status})
                raise ProviderUnavailable("Apify returned an unreadable response") from None
        if status == 401:
            raise ProviderKeyInvalid("Apify rejected the token (401)")
        if status in (402, 403):
            logger.warning(
                "apify_run_refused", extra={"status": status, "error_type": _error_type(response)}
            )
            raise ProviderRejected(f"Apify refused the run call ({status})")
        logger.warning("apify_error_status", extra={"status": status})
        raise ProviderUnavailable(f"Apify returned HTTP {status}")

    async def start_run(
        self,
        token: SecretStr,
        actor: str,
        run_input: dict[str, Any],
        *,
        max_items: int,
        max_charge_usd: float,
    ) -> str:
        if not isinstance(actor, str) or not ACTOR_RE.fullmatch(actor):
            raise ValueError("actor must look like 'owner/name'")
        if max_items < 1 or not (math.isfinite(max_charge_usd) and max_charge_usd > 0):
            raise ValueError("every run needs a positive max_items and max_charge_usd")
        body = await self._run_call(
            "POST",
            f"/v2/acts/{actor.replace('/', '~')}/runs",
            token,
            ok=(200, 201),
            params={
                "maxItems": str(max_items),
                "maxTotalChargeUsd": format(Decimal(str(max_charge_usd)), "f"),
            },
            json=run_input,
        )
        run_id = _data(body, "start_run").get("id")
        if not isinstance(run_id, str) or not _ID_RE.fullmatch(run_id):
            logger.warning("apify_bad_response", extra={"call": "start_run"})
            raise ProviderUnavailable("Apify returned no run id")
        return run_id

    async def get_run(self, token: SecretStr, run_id: str) -> RunState:
        _check_id(run_id, "run_id")
        data = _data(await self._run_call("GET", f"/v2/actor-runs/{run_id}", token), "get_run")
        raw_status = data.get("status")
        status = _STATUSES.get(raw_status) if isinstance(raw_status, str) else None
        if status is None:
            logger.warning("apify_bad_response", extra={"call": "get_run"})
            raise ProviderUnavailable("Apify returned an unknown run status")
        usage = data.get("usageTotalUsd")
        dataset_id = data.get("defaultDatasetId")
        usage_usd = (
            float(usage)
            if isinstance(usage, (int, float))
            and not isinstance(usage, bool)
            and math.isfinite(usage)
            and usage >= 0
            else None
        )
        return RunState(
            status=status,
            usage_usd=usage_usd,
            dataset_id=dataset_id
            if isinstance(dataset_id, str) and _ID_RE.fullmatch(dataset_id)
            else None,
        )

    async def fetch_items(
        self, token: SecretStr, dataset_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        _check_id(dataset_id, "dataset_id")
        if limit < 1:
            return []
        body = await self._run_call(
            "GET",
            f"/v2/datasets/{dataset_id}/items",
            token,
            params={"format": "json", "clean": "true", "limit": str(limit)},
        )
        if not isinstance(body, list):
            logger.warning("apify_bad_response", extra={"call": "fetch_items"})
            raise ProviderUnavailable("Apify returned an unexpected dataset response")
        return [item for item in body[:limit] if isinstance(item, dict)]

    async def abort_run(self, token: SecretStr, run_id: str) -> None:
        _check_id(run_id, "run_id")
        try:
            await self._run_call("POST", f"/v2/actor-runs/{run_id}/abort", token)
        except Exception as exc:  # best effort: never fails the caller
            logger.warning("apify_abort_failed", extra={"exc_type": type(exc).__name__})


def from_settings(settings: Settings | None = None) -> ApifyProviderRunner:
    """The production adapter, from ``APIFY_API_BASE`` and ``APIFY_INTEGRATION_PLATFORM``."""
    settings = settings or get_settings()
    return ApifyProviderRunner(settings.apify_api_base, settings.apify_integration_platform)
