"""AD-5 / AD-14: token verification and route classes."""

import base64
import hashlib
import hmac
import json
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient

from app.core.auth import JwksCache, verify_token
from app.core.config import get_settings
from app.core.errors import AuthUnavailableError, UnauthorizedError
from app.main import include_api_router
from tests.conftest import JWT_SECRET, AccountFactory, TokenFactory

ME = "/api/v1/me"
ADMIN_ME = "/api/v1/admin/me"
PROTECTED = (ME, ADMIN_ME)


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _assert_problem(resp, status: int, slug: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == f"/problems/{slug}"
    assert body["status"] == status


# --------------------------------------------------------------------------- public / no token


async def test_public_health_needs_no_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/public/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_old_health_path_is_gone(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/health")).status_code == 404


@pytest.mark.parametrize("path", PROTECTED)
async def test_missing_token_is_401(client: AsyncClient, path: str) -> None:
    _assert_problem(await client.get(path), 401, "unauthorized")


@pytest.mark.parametrize("path", PROTECTED)
async def test_non_bearer_scheme_is_401(client: AsyncClient, path: str) -> None:
    _assert_problem(await client.get(path, headers={"Authorization": "Basic abc"}), 401, "unauthorized")


async def test_new_module_route_is_protected_by_default(
    app: FastAPI, client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    """AC: a module route added under /api/v1 with no explicit auth returns 401 without a token."""
    module_router = APIRouter()

    @module_router.get("/widgets")
    async def widgets() -> dict[str, str]:
        return {"status": "ok"}

    include_api_router(app, module_router)
    app.openapi_schema = None
    _assert_problem(await client.get("/api/v1/widgets"), 401, "unauthorized")
    sub, email = accounts.new_identity()
    ok = await client.get("/api/v1/widgets", headers=tokens.headers(tokens.token(sub, email)))
    assert ok.status_code == 200


def test_module_router_cannot_smuggle_admin_or_public_paths(app: FastAPI) -> None:
    for path in ("/admin/x", "/public/x"):
        smuggler = APIRouter()
        smuggler.add_api_route(path, lambda: None)
        with pytest.raises(ValueError):
            include_api_router(app, smuggler)


# --------------------------------------------------------------------------- bad tokens (HTTP)


@pytest.mark.parametrize("path", PROTECTED)
@pytest.mark.parametrize(
    "case",
    ["bad-signature", "wrong-aud", "wrong-iss", "alg-none", "expired", "garbage", "no-exp"],
)
async def test_bad_tokens_are_401(
    client: AsyncClient, tokens: TokenFactory, path: str, case: str
) -> None:
    sub, email = uuid.uuid4(), "someone@example.test"
    if case == "bad-signature":
        token = jwt.encode(tokens.claims(sub, email), "x" * 40, algorithm="HS256")
    elif case == "wrong-aud":
        token = tokens.token(sub, email, aud="anon")
    elif case == "wrong-iss":
        token = tokens.token(sub, email, iss="http://evil.test/auth/v1")
    elif case == "alg-none":
        token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(tokens.claims(sub, email))}."
    elif case == "expired":
        token = tokens.token(sub, email, exp=1_000_000_000)
    elif case == "garbage":
        token = "not-a-jwt"
    else:
        claims = tokens.claims(sub, email)
        claims.pop("exp")
        token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    _assert_problem(await client.get(path, headers=tokens.headers(token)), 401, "unauthorized")


# --------------------------------------------------------------------------- verify_token


async def test_hs256_token_verifies(tokens: TokenFactory) -> None:
    sub = uuid.uuid4()
    verified = await verify_token(tokens.token(sub, "  Mixed.Case@Example.TEST "), JwksCache(tokens.fetch_jwks))
    assert verified.sub == sub
    assert verified.email == "mixed.case@example.test"


async def test_es256_token_verifies_via_jwks(tokens: TokenFactory) -> None:
    sub = uuid.uuid4()
    cache = JwksCache(tokens.fetch_jwks)
    verified = await verify_token(tokens.token(sub, "a@example.test", alg="ES256"), cache)
    assert verified.sub == sub
    # Cached: a second token does not refetch.
    await verify_token(tokens.token(sub, "a@example.test", alg="ES256"), cache)
    assert tokens.jwks_fetches == 1


async def test_unknown_kid_refreshes_jwks_once(tokens: TokenFactory) -> None:
    now = [1000.0]
    cache = JwksCache(tokens.fetch_jwks, min_refresh_interval=30.0, clock=lambda: now[0])
    await verify_token(tokens.token(uuid.uuid4(), "a@example.test", alg="ES256"), cache)
    assert tokens.jwks_fetches == 1

    # Rotation: Supabase publishes a new key; a token signed with it has an unknown kid.
    old = tokens.jwks()["keys"][0]
    tokens.ec_private_key = ec.generate_private_key(ec.SECP256R1())
    rotated = tokens.token(uuid.uuid4(), "a@example.test", alg="ES256", kid="rotated-key")
    new = tokens.jwks()["keys"][0] | {"kid": "rotated-key"}

    async def fetch_rotated() -> dict:
        tokens.jwks_fetches += 1
        return {"keys": [old, new]}

    cache._fetch = fetch_rotated
    now[0] += 31
    await verify_token(rotated, cache)
    assert tokens.jwks_fetches == 2

    # A forged kid right after a refresh does not trigger another fetch (rate limit).
    with pytest.raises(UnauthorizedError):
        await verify_token(tokens.token(uuid.uuid4(), "a@example.test", alg="ES256", kid="forged"), cache)
    assert tokens.jwks_fetches == 2


async def test_jwks_unreachable_is_503_not_401(tokens: TokenFactory) -> None:
    async def broken() -> dict:
        raise OSError("connection refused")

    with pytest.raises(AuthUnavailableError):
        await verify_token(tokens.token(uuid.uuid4(), "a@example.test", alg="ES256"), JwksCache(broken))


async def test_jwks_unreachable_is_503_over_http(
    app: FastAPI, client: AsyncClient, tokens: TokenFactory
) -> None:
    async def broken() -> dict:
        raise OSError("connection refused")

    app.state.jwks = JwksCache(broken)
    token = tokens.token(uuid.uuid4(), "a@example.test", alg="ES256")
    _assert_problem(await client.get(ME, headers=tokens.headers(token)), 503, "auth-unavailable")


async def test_refresh_failure_keeps_serving_cached_keys(tokens: TokenFactory) -> None:
    now = [1000.0]
    calls = {"n": 0}

    async def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("supabase down")
        return tokens.jwks()

    cache = JwksCache(flaky, ttl=60.0, min_refresh_interval=30.0, clock=lambda: now[0])
    token = tokens.token(uuid.uuid4(), "a@example.test", alg="ES256")
    await verify_token(token, cache)
    now[0] += 61  # cache is stale -> refresh attempt fails -> cached key still served
    assert (await verify_token(token, cache)).email == "a@example.test"
    assert calls["n"] == 2


async def test_failed_fetch_not_retried_within_interval(tokens: TokenFactory) -> None:
    now = [1000.0]
    calls = {"n": 0}

    async def broken() -> dict:
        calls["n"] += 1
        raise OSError("supabase down")

    cache = JwksCache(broken, min_refresh_interval=30.0, clock=lambda: now[0])
    token = tokens.token(uuid.uuid4(), "a@example.test", alg="ES256")
    for _ in range(3):
        with pytest.raises(AuthUnavailableError):
            await verify_token(token, cache)
    assert calls["n"] == 1
    now[0] += 31
    with pytest.raises(AuthUnavailableError):
        await verify_token(token, cache)
    assert calls["n"] == 2


@pytest.mark.parametrize(
    "jwks",
    [
        {"keys": None},
        {"keys": "nope"},
        [],
        {"keys": [{"kid": "test-es256-key", "kty": "EC", "crv": "P-256", "x": "!!", "y": 1}]},
        {"keys": [{"kid": "test-es256-key", "kty": "RSA", "n": None, "e": None}]},
        {"keys": [{"kid": 7, "kty": "EC"}, "junk", None]},
    ],
    ids=["keys-null", "keys-str", "not-object", "bad-ec", "bad-rsa", "junk-entries"],
)
async def test_malformed_jwks_is_401_not_500(tokens: TokenFactory, jwks: object) -> None:
    async def fetch() -> object:
        return jwks

    with pytest.raises(UnauthorizedError):
        await verify_token(
            tokens.token(uuid.uuid4(), "a@example.test", alg="ES256"), JwksCache(fetch)  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("alg", [7, ["ES256"], {"x": 1}, True])
async def test_non_string_alg_is_rejected(tokens: TokenFactory, alg: object) -> None:
    token = f"{_b64({'alg': alg, 'typ': 'JWT'})}.{_b64(tokens.claims(uuid.uuid4(), 'a@example.test'))}.c2ln"
    with pytest.raises(UnauthorizedError):
        await verify_token(token, JwksCache(tokens.fetch_jwks))


async def test_small_clock_skew_is_tolerated(tokens: TokenFactory) -> None:
    import time

    token = tokens.token(uuid.uuid4(), "a@example.test", exp=int(time.time()) - 10)
    assert (await verify_token(token, JwksCache(tokens.fetch_jwks))).email == "a@example.test"


async def test_hs256_signed_with_public_key_is_rejected(tokens: TokenFactory) -> None:
    """Algorithm confusion: an HS256 token keyed with the public JWK must not verify."""
    public_pem = tokens.ec_private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _b64({"alg": "HS256", "typ": "JWT", "kid": "test-es256-key"})
    payload = _b64(tokens.claims(uuid.uuid4(), "a@example.test"))
    signing_input = f"{header}.{payload}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(public_pem, signing_input, hashlib.sha256).digest()).rstrip(b"=")
    with pytest.raises(UnauthorizedError):
        await verify_token(f"{header}.{payload}.{sig.decode()}", JwksCache(tokens.fetch_jwks))


async def test_es256_header_with_hs256_jwk_mismatch_is_rejected(tokens: TokenFactory) -> None:
    """The JWK found by kid must be for the algorithm in the header."""
    token = tokens.token(uuid.uuid4(), "a@example.test", alg="ES256")
    header, payload, sig = token.split(".")
    forged_header = _b64({"alg": "RS256", "typ": "JWT", "kid": "test-es256-key"})
    with pytest.raises(UnauthorizedError):
        await verify_token(f"{forged_header}.{payload}.{sig}", JwksCache(tokens.fetch_jwks))


@pytest.mark.parametrize("alg", ["HS384", "HS512", "PS256", "EdDSA", "none", None])
async def test_other_algorithms_are_rejected(tokens: TokenFactory, alg: str | None) -> None:
    header = {"typ": "JWT"} | ({"alg": alg} if alg else {})
    token = f"{_b64(header)}.{_b64(tokens.claims(uuid.uuid4(), 'a@example.test'))}.c2ln"
    with pytest.raises(UnauthorizedError):
        await verify_token(token, JwksCache(tokens.fetch_jwks))


async def test_hs256_rejected_when_no_secret_configured(
    tokens: TokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tokens.token(uuid.uuid4(), "a@example.test")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(UnauthorizedError):
        await verify_token(token, JwksCache(tokens.fetch_jwks))


@pytest.mark.parametrize(
    "overrides",
    [{"sub": None}, {"email": None}, {"email": "  "}, {"sub": "not-a-uuid"}, {"email": 42}],
    ids=["no-sub", "no-email", "blank-email", "sub-not-uuid", "email-not-str"],
)
async def test_token_without_usable_sub_or_email_is_rejected(
    tokens: TokenFactory, overrides: dict
) -> None:
    claims = tokens.claims(uuid.uuid4(), "a@example.test")
    for key, value in overrides.items():
        if value is None:
            claims.pop(key)
        else:
            claims[key] = value
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    with pytest.raises(UnauthorizedError):
        await verify_token(token, JwksCache(tokens.fetch_jwks))


async def test_token_without_sub_is_401_over_http(client: AsyncClient, tokens: TokenFactory) -> None:
    claims = tokens.claims(uuid.uuid4(), "a@example.test")
    claims.pop("sub")
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    _assert_problem(await client.get(ME, headers=tokens.headers(token)), 401, "unauthorized")


# --------------------------------------------------------------------------- roles


async def test_admin_claim_spoof_is_403(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    account_id = accounts.create(role="student")
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    token = tokens.token(
        account_id,
        email,
        role="admin",
        app_metadata={"role": "admin"},
        user_metadata={"role": "admin"},
    )
    _assert_problem(await client.get(ADMIN_ME, headers=tokens.headers(token)), 403, "forbidden")
    me = await client.get(ME, headers=tokens.headers(token))
    assert me.json()["role"] == "student"
    assert accounts.row(account_id)[2] == "student"  # type: ignore[index]


async def test_admin_role_from_db_gets_admin_route(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    account_id = accounts.create(role="admin")
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    resp = await client.get(ADMIN_ME, headers=tokens.headers(tokens.token(account_id, email, alg="ES256")))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": str(account_id), "email": email, "role": "admin"}
