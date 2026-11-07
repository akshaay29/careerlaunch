"""AD-1 / AD-13: schema `app`, search_path, migrations, and the exposed-schema check."""

import psycopg
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import to_libpq_url
from migrations.helpers import app_table_rls_sql
from scripts.check_exposed_schema import find_violations
from tests.conftest import alembic_config


def _version(url: str) -> list[str]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return [r[0] for r in conn.execute("SELECT version_num FROM app.alembic_version")]


async def test_session_search_path_is_app(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW search_path"))).scalar_one() == "app"
        assert (await conn.execute(text("SELECT current_schema()"))).scalar_one() == "app"


def test_upgrade_downgrade_upgrade(migrated_db: str) -> None:
    cfg = alembic_config()
    assert _version(migrated_db) == ["0001_app_schema"]
    command.downgrade(cfg, "base")
    assert _version(migrated_db) == []
    command.upgrade(cfg, "head")
    assert _version(migrated_db) == ["0001_app_schema"]


def test_app_schema_locked_down(sync_conn: psycopg.Connection) -> None:
    for role in ("anon", "authenticated"):
        (usage,) = sync_conn.execute(
            "SELECT has_schema_privilege(%s, 'app', 'USAGE')", (role,)
        ).fetchone()  # type: ignore[misc]
        assert usage is False
    (rls,) = sync_conn.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'app.alembic_version'::regclass"
    ).fetchone()  # type: ignore[misc]
    assert rls is True


def test_exposed_schema_check_passes(sync_conn: psycopg.Connection) -> None:
    assert find_violations(sync_conn) == []


def test_exposed_schema_check_fails_on_public_table(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE TABLE public.leak (id int)")
    assert any("public.leak" in v for v in find_violations(sync_conn))


def test_exposed_schema_check_fails_without_rls(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE TABLE app.no_rls (id int)")
    assert any("app.no_rls does not have RLS" in v for v in find_violations(sync_conn))


def test_rls_helper_satisfies_check(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE TABLE app.with_rls (id int)")
    for stmt in app_table_rls_sql("with_rls"):
        sync_conn.execute(stmt)
    assert find_violations(sync_conn) == []
    (forced,) = sync_conn.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'app.with_rls'::regclass"
    ).fetchone()  # type: ignore[misc]
    assert forced is True


@pytest.mark.parametrize(
    ("grant", "needle"),
    [
        ("GRANT USAGE ON SCHEMA app TO anon", "anon has USAGE on schema app"),
        ("GRANT SELECT ON app.alembic_version TO authenticated", "authenticated has SELECT"),
        ("GRANT SELECT ON app.alembic_version TO PUBLIC", "anon has SELECT"),
    ],
)
def test_exposed_schema_check_fails_on_grants(
    sync_conn: psycopg.Connection, grant: str, needle: str
) -> None:
    sync_conn.execute(grant)
    assert any(needle in v for v in find_violations(sync_conn))
