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


HEAD = "0008_preferences"
JOB_TABLES = {
    "job_outbox",
    "schedules",
    "ops_events",
    "drain_state",
    "procrastinate_jobs",
    "procrastinate_workers",
    "procrastinate_events",
    "procrastinate_periodic_defers",
}


def test_upgrade_downgrade_upgrade(migrated_db: str) -> None:
    cfg = alembic_config()
    assert _version(migrated_db) == [HEAD]
    command.downgrade(cfg, "base")
    assert _version(migrated_db) == []
    command.upgrade(cfg, "head")
    assert _version(migrated_db) == [HEAD]


def test_job_tables_in_app_with_forced_rls(sync_conn: psycopg.Connection) -> None:
    rows = sync_conn.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'app' AND c.relkind = 'r'
        """
    ).fetchall()
    tables = {name: (rls, forced) for name, rls, forced in rows}
    assert JOB_TABLES <= set(tables)
    for name in JOB_TABLES:
        assert tables[name] == (True, True), name
    # Nothing landed outside `app` (e.g. Procrastinate objects in public).
    (leaked,) = sync_conn.execute(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE p.proname LIKE 'procrastinate%%' AND n.nspname <> 'app'"
    ).fetchone()  # type: ignore[misc]
    assert leaked == 0


def test_pg_cron_drain_job(sync_conn: psycopg.Connection) -> None:
    """On the Supabase CLI stack 0003 schedules the drain; on plain Postgres it skips."""
    (has_cron,) = sync_conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron')"
    ).fetchone()  # type: ignore[misc]
    (can_cron,) = sync_conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron') "
        "AND EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_net') "
        "AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'vault')"
    ).fetchone()  # type: ignore[misc]
    if not can_cron:
        if has_cron:
            (n,) = sync_conn.execute(
                "SELECT count(*) FROM cron.job WHERE jobname = 'careerlaunch-drain'"
            ).fetchone()  # type: ignore[misc]
            assert n == 0
        pytest.skip("pg_cron/pg_net/vault not available (plain Postgres): 0003 skipped by design")
    row = sync_conn.execute(
        "SELECT schedule, command FROM cron.job WHERE jobname = 'careerlaunch-drain'"
    ).fetchone()
    assert row is not None
    schedule, cmd = row
    assert schedule == "*/5 * * * *"
    assert "timeout_milliseconds := 90000" in cmd
    assert "vault.decrypted_secrets" in cmd
    assert "name = 'drain_url'" in cmd and "name = 'drain_secret'" in cmd
    assert "net.http_post" in cmd


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


_FN = "CREATE FUNCTION {name}() RETURNS int LANGUAGE sql AS 'SELECT 1'"


@pytest.mark.parametrize(
    ("statements", "needle"),
    [
        (["GRANT USAGE ON SCHEMA app TO anon"], "anon has USAGE on schema app"),
        (["GRANT CREATE ON SCHEMA app TO authenticated"], "authenticated has CREATE on schema app"),
        (["GRANT SELECT ON app.alembic_version TO authenticated"], "authenticated has SELECT"),
        (["GRANT SELECT ON app.alembic_version TO PUBLIC"], "anon has SELECT"),
        (
            ["GRANT SELECT (version_num) ON app.alembic_version TO anon"],
            "anon has column-level SELECT on app.alembic_version",
        ),
        (
            ["CREATE SEQUENCE app.seq_leak", "GRANT USAGE ON SEQUENCE app.seq_leak TO anon"],
            "anon has USAGE on sequence app.seq_leak",
        ),
        (
            [_FN.format(name="app.fn_leak"), "GRANT EXECUTE ON FUNCTION app.fn_leak() TO authenticated"],
            "authenticated has a grant on function app.fn_leak",
        ),
        ([_FN.format(name="public.rpc_leak")], "function public.rpc_leak is in an exposed schema"),
    ],
    ids=["schema-usage", "schema-create", "table", "public-role", "column", "sequence",
         "function-grant", "public-function"],
)
def test_exposed_schema_check_fails(
    sync_conn: psycopg.Connection, statements: list[str], needle: str
) -> None:
    for stmt in statements:
        sync_conn.execute(stmt)
    assert any(needle in v for v in find_violations(sync_conn))


def test_exposed_schema_check_requires_rls_bypassing_role(sync_conn: psycopg.Connection) -> None:
    (bypasses,) = sync_conn.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()  # type: ignore[misc]
    assert bypasses, "tests must connect as a role that bypasses RLS"
    sync_conn.execute("CREATE ROLE cl_no_bypass NOLOGIN NOBYPASSRLS")
    # Non-superusers (Supabase's `postgres`) need membership to SET ROLE.
    sync_conn.execute("GRANT cl_no_bypass TO CURRENT_USER")
    sync_conn.execute("SET ROLE cl_no_bypass")
    try:
        assert any("cannot bypass RLS" in v for v in find_violations(sync_conn))
    finally:
        sync_conn.execute("RESET ROLE")
