"""Identity (AD-5, AD-14): Supabase JWT verification, the request actor and route-class guards.

* :func:`verify_token` checks the signature, ``exp``, ``aud == "authenticated"`` and
  ``iss == <SUPABASE_URL>/auth/v1``. Accepted algorithms: ES256 / RS256 with a key from the
  project's JWKS (cached, refreshed on an unknown ``kid``) and HS256 with
  ``SUPABASE_JWT_SECRET`` (legacy; the local CLI). Anything else, ``none`` included, is a 401.
* The role is NEVER read from the token. :func:`current_actor` hands the verified ``sub`` and
  ``email`` to the account loader installed at app wiring (``accounts.service``), which
  provisions the ``accounts`` row on first sight and returns the role from the database.
  ``core`` therefore never imports ``app.modules``.
* Route classes are applied by router-level dependencies in ``app.main``: ``/api/v1/*`` uses
  :func:`current_actor`, ``/api/v1/admin/*`` uses :func:`require_admin`.
"""

import asyncio
import json
import logging
import time
import urllib.request
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AuthUnavailableError, ForbiddenError, UnauthorizedError

logger = logging.getLogger("app.auth")

AUDIENCE = "authenticated"
ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256"})
HMAC_ALGORITHM = "HS256"
# Tolerated clock difference between Supabase Auth and this host for exp/iat/nbf.
CLOCK_SKEW_LEEWAY_SECONDS = 30

Role = Literal["student", "admin"]


@dataclass(frozen=True)
class Actor:
    """Who is calling. ``role`` always comes from ``accounts``, never from token claims."""

    id: uuid.UUID
    email: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class VerifiedToken:
    """The only claims the app trusts: the Auth uid and the (lower-cased) email."""

    sub: uuid.UUID
    email: str


# (session, account_id, email) -> (Actor, wrote). ``wrote`` is True when the loader inserted or
# updated the accounts row, i.e. there is something to commit. Installed by app wiring.
AccountLoader = Callable[[AsyncSession, uuid.UUID, str], Awaitable[tuple[Actor, bool]]]
JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


# --------------------------------------------------------------------------- JWKS cache


def _fetch_jwks_sync(url: str, timeout: float) -> dict[str, Any]:
    if not url.startswith(("https://", "http://")):
        raise ValueError("JWKS URL must be http(s)")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (scheme checked)
        return json.loads(response.read().decode("utf-8"))


async def fetch_jwks_from_settings() -> dict[str, Any]:
    """Default fetcher: ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``."""
    return await asyncio.to_thread(_fetch_jwks_sync, get_settings().supabase_jwks_url, 5.0)


class JwksCache:
    """Caches the project's signing keys by ``kid``.

    Keys are reused for ``ttl`` seconds. A stale cache or an unknown ``kid`` triggers a
    refresh, but fetch ATTEMPTS (successful or not) are at most one per
    ``min_refresh_interval`` seconds, so forged ``kid`` values or a Supabase outage cannot turn
    into a stream of requests. A failed refresh keeps serving the keys already cached.
    """

    def __init__(
        self,
        fetch: JwksFetcher = fetch_jwks_from_settings,
        *,
        ttl: float = 600.0,
        min_refresh_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._last_failed = False
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at < self._ttl

    def _may_refresh(self) -> bool:
        return (
            self._attempted_at is None
            or self._clock() - self._attempted_at >= self._min_refresh_interval
        )

    def _unavailable_if_failed(self) -> None:
        if self._last_failed and not self._keys:
            raise AuthUnavailableError("The sign-in keys could not be loaded.")

    async def _refresh(self) -> None:
        self._attempted_at = self._clock()
        try:
            raw = await self._fetch()
        except Exception as exc:
            self._last_failed = True
            logger.warning("jwks_fetch_failed", extra={"exc_type": type(exc).__name__})
            self._unavailable_if_failed()
            return  # keep serving the keys we have
        entries = raw.get("keys") if isinstance(raw, dict) else None
        keys: dict[str, jwt.PyJWK] = {}
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("kid"), str):
                continue
            try:
                key = jwt.PyJWK(entry)
            except (jwt.PyJWKError, jwt.InvalidKeyError, ValueError, TypeError):
                continue  # unsupported or malformed key: skip it, keep the others
            if key.algorithm_name in ASYMMETRIC_ALGORITHMS:
                keys[entry["kid"]] = key
        self._keys = keys
        self._fetched_at = self._clock()
        self._last_failed = False

    async def get(self, kid: str) -> jwt.PyJWK | None:
        if self._fresh() and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            # Re-check: another request may have refreshed while we waited.
            if self._fresh() and kid in self._keys:
                return self._keys[kid]
            if self._may_refresh():
                await self._refresh()
            else:
                self._unavailable_if_failed()
            return self._keys.get(kid)


# --------------------------------------------------------------------------- verification


def _unauthorized(detail: str = "A valid access token is required.") -> UnauthorizedError:
    return UnauthorizedError(detail)


async def verify_token(token: str, jwks: JwksCache) -> VerifiedToken:
    """Verify a Supabase access token. Raises :class:`UnauthorizedError` on any problem."""
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized() from exc
    alg = header.get("alg")
    if not isinstance(alg, str):
        raise _unauthorized()

    key: Any
    if alg == HMAC_ALGORITHM:
        if settings.supabase_jwt_secret is None:
            raise _unauthorized()
        key = settings.supabase_jwt_secret.get_secret_value().encode("utf-8")
    elif alg in ASYMMETRIC_ALGORITHMS:
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _unauthorized()
        jwk = await jwks.get(kid)
        # The key must exist and be a key for exactly the algorithm the token claims.
        if jwk is None or jwk.algorithm_name != alg:
            raise _unauthorized()
        key = jwk.key
    else:
        raise _unauthorized()  # none, HS384, PS256, ... are all rejected

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=AUDIENCE,
            issuer=settings.supabase_issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized() from exc

    sub, email = claims.get("sub"), claims.get("email")
    if not isinstance(sub, str) or not isinstance(email, str) or not email.strip():
        raise _unauthorized()
    try:
        account_id = uuid.UUID(sub)
    except ValueError as exc:
        raise _unauthorized() from exc
    return VerifiedToken(sub=account_id, email=email.strip().lower())


# --------------------------------------------------------------------------- dependencies


def install_auth(
    app: FastAPI, account_loader: AccountLoader, jwks: JwksCache | None = None
) -> None:
    """Wire the account loader (from ``accounts.service``) and the JWKS cache into ``app``."""
    app.state.account_loader = account_loader
    app.state.jwks = jwks or JwksCache()


# Declares the bearer scheme in OpenAPI (the generated web client attaches the token from it).
# auto_error=False: a missing or non-bearer header reaches us as None and becomes a 401 problem.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="SupabaseAccessToken",
    description="Supabase Auth access token: `Authorization: Bearer <access_token>`.",
)


async def current_actor(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Actor:
    """``/api/v1/*`` guard: verify the bearer token, then load (or provision) the account.

    Provisioning is this dependency's own unit of work: it is committed here only when the
    loader actually inserted or updated the account row (the common read-only path commits
    nothing).
    """
    if credentials is None or not credentials.credentials.strip():
        raise _unauthorized()
    verified = await verify_token(credentials.credentials.strip(), request.app.state.jwks)
    loader: AccountLoader = request.app.state.account_loader
    actor, wrote = await loader(session, verified.sub, verified.email)
    if wrote:
        await session.commit()
    return actor


async def require_admin(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    """``/api/v1/admin/*`` guard: the database role must be ``admin``."""
    if not actor.is_admin:
        raise ForbiddenError("This route requires the admin role.")
    return actor
