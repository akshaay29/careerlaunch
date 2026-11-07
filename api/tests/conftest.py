"""Shared fixtures.

DB tests run against DATABASE_URL (CI: a postgres:17 service with tests/sql/supabase_roles.sql
applied; locally: the Supabase CLI Postgres on :54322). The ``migrated_db`` fixture brings
it to ``alembic upgrade head`` once per session.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine

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
