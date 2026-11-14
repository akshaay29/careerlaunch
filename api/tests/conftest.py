"""Shared fixtures.

DB tests run against DATABASE_URL (CI: a postgres:17 service with tests/sql/supabase_roles.sql
applied; locally: the Supabase CLI Postgres on :54322). The ``migrated_db`` fixture brings
it to ``alembic upgrade head`` once per session.
"""

import asyncio
import json
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path

import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.auth import JwksCache
from app.core.config import get_settings, to_libpq_url
from app.core.db import make_engine
from app.main import create_app

API_DIR = Path(__file__).resolve().parents[1]


if sys.platform == "win32":
    # psycopg's async driver cannot use Windows' default Proactor loop. Defined only on
    # Windows: pytest-asyncio rejects a hook implementation that returns None.
    def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> Mapping:
        return {"selector": asyncio.SelectorEventLoop}


def pytest_configure(config: pytest.Config) -> None:
    if get_settings().environment == "prod":
        pytest.exit("Refusing to run tests with ENVIRONMENT=prod", returncode=2)


def alembic_config() -> Config:
    """Alembic config pinned to the TEST database (never MIGRATION_DATABASE_URL)."""
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = get_settings().sqlalchemy_url
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    return get_settings().sqlalchemy_url


@pytest.fixture(scope="session")
def migrated_db(db_url: str) -> str:
    command.upgrade(alembic_config(), "head")
    return db_url


@pytest.fixture
def sync_conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    """A psycopg connection whose transaction is always rolled back."""
    with psycopg.connect(to_libpq_url(migrated_db)) as conn:
        try:
            yield conn
        finally:
            conn.rollback()


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(migrated_db, poolclass=pool.NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


JOB_TABLES = (
    "job_outbox",
    "schedules",
    "ops_events",
    "drain_state",
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)


@pytest.fixture
def clean_jobs(migrated_db: str) -> Iterator[str]:
    """Empty every job/ops table before (and after) the test."""

    def _truncate() -> None:
        with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
            conn.execute(f"TRUNCATE {', '.join('app.' + t for t in JOB_TABLES)} CASCADE")

    _truncate()
    yield migrated_db
    _truncate()


DRAIN_SECRET = "test-drain-secret-current"
DRAIN_SECRET_PREVIOUS = "test-drain-secret-previous"


@pytest.fixture
def drain_secrets(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    """Configure DRAIN_SECRET / DRAIN_SECRET_PREVIOUS for the test (Settings cache reset)."""
    monkeypatch.setenv("DRAIN_SECRET", DRAIN_SECRET)
    monkeypatch.setenv("DRAIN_SECRET_PREVIOUS", DRAIN_SECRET_PREVIOUS)
    get_settings.cache_clear()
    yield DRAIN_SECRET, DRAIN_SECRET_PREVIOUS
    monkeypatch.undo()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- auth (Story 1.3)

SUPABASE_URL = "http://supabase.test"
JWT_SECRET = "test-supabase-jwt-secret-with-at-least-32-characters"
ES256_KID = "test-es256-key"


class TokenFactory:
    """Signs test access tokens the way Supabase Auth does (HS256 secret or ES256 JWKS key)."""

    def __init__(self) -> None:
        self.ec_private_key = ec.generate_private_key(ec.SECP256R1())
        self.jwks_fetches = 0

    @property
    def issuer(self) -> str:
        return f"{SUPABASE_URL}/auth/v1"

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.ec_private_key.public_key()))
        jwk.update({"kid": ES256_KID, "alg": "ES256", "use": "sig"})
        return {"keys": [jwk]}

    async def fetch_jwks(self) -> dict:
        self.jwks_fetches += 1
        return self.jwks()

    def claims(self, sub: uuid.UUID | str, email: str | None, **overrides: object) -> dict:
        now = int(time.time())
        claims: dict = {
            "sub": str(sub),
            "email": email,
            "aud": "authenticated",
            "iss": self.issuer,
            "iat": now,
            "exp": now + 3600,
            "role": "authenticated",
        }
        claims.update(overrides)
        return {k: v for k, v in claims.items() if v is not None}

    def token(
        self,
        sub: uuid.UUID | str,
        email: str | None,
        *,
        alg: str = "HS256",
        kid: str | None = None,
        **overrides: object,
    ) -> str:
        payload = self.claims(sub, email, **overrides)
        if alg == "HS256":
            return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        if alg == "ES256":
            return jwt.encode(
                payload, self.ec_private_key, algorithm="ES256", headers={"kid": kid or ES256_KID}
            )
        raise ValueError(alg)

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch, app) -> Iterator[TokenFactory]:
    """Point Settings at a fake Supabase project and give ``app`` a fake JWKS endpoint."""
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    factory = TokenFactory()
    app.state.jwks = JwksCache(factory.fetch_jwks)
    yield factory
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def accounts(migrated_db: str) -> Iterator["AccountFactory"]:
    """Create ``accounts`` rows directly; every row created here is deleted afterwards."""
    factory = AccountFactory(migrated_db)
    yield factory
    factory.cleanup()


class AccountFactory:
    def __init__(self, url: str) -> None:
        self.url = to_libpq_url(url)
        self.ids: list[uuid.UUID] = []

    def track(self, account_id: uuid.UUID) -> uuid.UUID:
        self.ids.append(account_id)
        return account_id

    def new_identity(self) -> tuple[uuid.UUID, str]:
        """A fresh (uid, email) pair, tracked for cleanup, with no row yet."""
        account_id = self.track(uuid.uuid4())
        return account_id, f"user-{account_id.hex[:12]}@example.test"

    def create(self, *, role: str = "student", email: str | None = None) -> uuid.UUID:
        account_id, default_email = self.new_identity()
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.accounts (id, email, role) VALUES (%s, %s, %s)",
                (account_id, email or default_email, role),
            )
        return account_id

    def row(self, account_id: uuid.UUID) -> tuple | None:
        with psycopg.connect(self.url) as conn:
            return conn.execute(
                "SELECT id, email, role FROM app.accounts WHERE id = %s", (account_id,)
            ).fetchone()

    def cleanup(self) -> None:
        if not self.ids:
            return
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("DELETE FROM app.accounts WHERE id = ANY(%s)", (self.ids,))
