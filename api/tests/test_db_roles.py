"""AD-1 / NFR-8: no table is reachable with the Supabase anon key (Story 4.4).

CI has no PostgREST, so a real read as each API role stands in for "an anon REST request to
each table is refused": for every table in schema ``app`` (from ``pg_tables``), ``SET LOCAL
ROLE anon`` (then ``authenticated``) and ``SELECT 1 FROM app.<table> LIMIT 1`` must fail with a
permission error. ``scripts/check_exposed_schema.py`` runs the same probe.
"""

import psycopg
import pytest

from scripts.check_exposed_schema import (
    API_ROLES,
    PROBE_RELATION,
    find_violations,
    probe_read,
    role_read_violations,
)


def _app_tables(conn: psycopg.Connection) -> list[str]:
    return [
        t for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'app'")
    ]


def test_there_are_app_tables_to_probe(sync_conn: psycopg.Connection) -> None:
    tables = set(_app_tables(sync_conn))
    assert {"accounts", "applications", "listings", "procrastinate_jobs", "ops_events"} <= tables


@pytest.mark.parametrize("role", API_ROLES)
def test_every_app_table_refuses_the_api_roles(sync_conn: psycopg.Connection, role: str) -> None:
    not_refused = {}
    for table in _app_tables(sync_conn):
        outcome = probe_read(sync_conn, role, "app", table)
        if outcome != "refused":
            not_refused[table] = outcome
    assert not not_refused, f"{role} was not refused on: {not_refused}"


def test_the_refusal_is_a_permission_error(sync_conn: psycopg.Connection) -> None:
    """The probe's own steps, spelled out once: a permission error, not an empty result."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with sync_conn.transaction(force_rollback=True):
            sync_conn.execute("SET LOCAL ROLE anon")
            sync_conn.execute("SELECT 1 FROM app.accounts LIMIT 1")
    # The role ended with the savepoint.
    (user,) = sync_conn.execute("SELECT current_user").fetchone()  # type: ignore[misc]
    assert user not in API_ROLES


@pytest.mark.parametrize("role", API_ROLES)
def test_public_reads_fail(sync_conn: psycopg.Connection, role: str) -> None:
    exposed = sync_conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ).fetchall()
    for (table,) in exposed:
        assert probe_read(sync_conn, role, "public", table) != "read", table
    assert probe_read(sync_conn, role, "public", PROBE_RELATION) != "read"


def test_the_role_probe_passes_on_the_migrated_schema(sync_conn: psycopg.Connection) -> None:
    assert role_read_violations(sync_conn) == []
    assert find_violations(sync_conn) == []


def test_the_probe_catches_a_readable_table(sync_conn: psycopg.Connection) -> None:
    """A grant that slips through (rolled back after the test) is caught even though forced
    RLS would return no rows: a read must be refused, not merely empty."""
    sync_conn.execute("GRANT USAGE ON SCHEMA app TO anon")
    sync_conn.execute("GRANT SELECT ON app.accounts TO anon")
    assert probe_read(sync_conn, "anon", "app", "accounts") == "read"
    violations = role_read_violations(sync_conn)
    assert "anon reading app.accounts was not refused (read)" in violations
    assert not any(v.startswith("authenticated") for v in violations)


def test_the_probe_catches_a_readable_public_table(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE TABLE public.cl_leak (id int)")
    sync_conn.execute("GRANT SELECT ON public.cl_leak TO authenticated")
    assert "authenticated can read public.cl_leak" in role_read_violations(sync_conn)


def test_a_view_in_app_is_probed_too(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE VIEW app.cl_probe_view AS SELECT 1 AS one")
    sync_conn.execute("GRANT USAGE ON SCHEMA app TO authenticated")
    sync_conn.execute("GRANT SELECT ON app.cl_probe_view TO authenticated")
    assert (
        "authenticated reading app.cl_probe_view was not refused (read)"
        in role_read_violations(sync_conn)
    )


def test_an_error_other_than_permission_denied_is_a_violation(
    sync_conn: psycopg.Connection,
) -> None:
    """A relation that fails for another reason (here a view that divides by zero when
    read) is reported with its SQLSTATE, not taken as refused."""
    sync_conn.execute(
        "CREATE VIEW app.cl_probe_err AS "
        "SELECT g FROM generate_series(1, 1) AS g WHERE 1 / (g - 1) = 0"
    )
    sync_conn.execute("GRANT USAGE ON SCHEMA app TO anon")
    sync_conn.execute("GRANT SELECT ON app.cl_probe_err TO anon")
    assert "anon reading app.cl_probe_err was not refused (error: 22012)" in (
        role_read_violations(sync_conn)
    )


def test_the_probe_reports_when_it_cannot_set_role(sync_conn: psycopg.Connection) -> None:
    """Connected as a role that is not a member of anon / authenticated (created and used
    inside the test's rolled-back transaction), nothing can be probed: both are reported."""
    sync_conn.execute("CREATE ROLE cl_probe_outsider NOLOGIN")
    # SET ROLE checks the SESSION user's membership, so the session user must change.
    sync_conn.execute("SET LOCAL SESSION AUTHORIZATION cl_probe_outsider")
    violations = role_read_violations(sync_conn)
    for role in API_ROLES:
        assert any(v.startswith(f"cannot SET ROLE {role} ") for v in violations), violations
