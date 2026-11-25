"""AD-1 CI guardrail: nothing reachable through the Supabase Data API.

Exits 1 if:
  * any relation (table, view, sequence, index, ...) exists in ``public`` or ``graphql_public``;
  * any function/procedure exists there (PostgREST exposes them as RPC), except
    extension-owned ones and Supabase's own ``graphql_public.graphql`` entrypoint;
  * any table in schema ``app`` does not have row level security enabled;
  * ``anon`` or ``authenticated`` hold any privilege on schema ``app`` or its objects
    (directly, via role membership, or via PUBLIC);
  * the connected role is neither superuser nor BYPASSRLS (forced RLS would lock it out);
  * a real read as ``anon`` or ``authenticated`` (``SET LOCAL ROLE``, then
    ``SELECT 1 FROM <table> LIMIT 1``) of any table in schema ``app`` is not refused with a
    permission error, or a read of a relation in ``public`` (or of a made-up one there)
    succeeds. This stands in for an anon REST request per table: CI has no PostgREST.

Usage: ``uv run python scripts/check_exposed_schema.py`` (reads DATABASE_URL via Settings).
"""

import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings, to_libpq_url  # noqa: E402

EXPOSED_SCHEMAS = ("public", "graphql_public")
APP_SCHEMA = "app"
API_ROLES = ("anon", "authenticated")
# Platform-managed functions Supabase installs in exposed schemas (owner supabase_admin).
ALLOWED_EXPOSED_FUNCTIONS = {("graphql_public", "graphql")}
# A relation name that never exists: reading it as an API role must still fail.
PROBE_RELATION = "cl_role_probe_missing"
PERMISSION_DENIED = "42501"

_RELKIND = {
    "r": "table",
    "p": "partitioned table",
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
    "S": "sequence",
    "i": "index",
    "I": "partitioned index",
    "c": "composite type",
    "t": "TOAST table",
}


def find_violations(conn: psycopg.Connection) -> list[str]:
    violations: list[str] = []

    missing = conn.execute(
        "SELECT r FROM unnest(%s::text[]) AS r WHERE NOT EXISTS "
        "(SELECT 1 FROM pg_roles WHERE rolname = r)",
        (list(API_ROLES),),
    ).fetchall()
    if missing:
        roles = ", ".join(r for (r,) in missing)
        return [f"role(s) missing: {roles} (apply tests/sql/supabase_roles.sql on plain Postgres)"]

    # 0. App tables use FORCE ROW LEVEL SECURITY with no policies, so the role the API and
    #    migrations connect as must bypass RLS (superuser or BYPASSRLS), or every query fails.
    (bypasses,) = conn.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()  # type: ignore[misc]
    if not bypasses:
        violations.append(
            "connected role cannot bypass RLS (needs SUPERUSER or BYPASSRLS): forced RLS on "
            "schema app would block it"
        )

    # 1. Relations in exposed schemas.
    for nsp, rel, kind in conn.execute(
        """
        SELECT n.nspname, c.relname, c.relkind::text
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s)
        ORDER BY 1, 2
        """,
        (list(EXPOSED_SCHEMAS),),
    ):
        violations.append(f"{_RELKIND.get(kind, kind)} {nsp}.{rel} is in an exposed schema")

    # 1b. Functions / procedures in exposed schemas (callable as RPC), minus extension members.
    for nsp, fn, kind in conn.execute(
        """
        SELECT n.nspname, p.proname, p.prokind::text
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ANY(%s)
          AND NOT EXISTS (
            SELECT 1 FROM pg_depend d
            WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
          )
        ORDER BY 1, 2
        """,
        (list(EXPOSED_SCHEMAS),),
    ):
        if (nsp, fn) in ALLOWED_EXPOSED_FUNCTIONS:
            continue
        label = "procedure" if kind == "p" else "function"
        violations.append(f"{label} {nsp}.{fn} is in an exposed schema")

    schema_exists = conn.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname = %s", (APP_SCHEMA,)
    ).fetchone()
    if not schema_exists:
        violations.append(f"schema {APP_SCHEMA} does not exist (run alembic upgrade head)")
        return violations

    # 2. Every table in `app` has RLS enabled.
    for (rel,) in conn.execute(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity
        ORDER BY 1
        """,
        (APP_SCHEMA,),
    ):
        violations.append(f"table {APP_SCHEMA}.{rel} does not have RLS enabled")

    for role in API_ROLES:
        # 3a. Schema privileges (has_* covers PUBLIC and inherited membership).
        for priv in ("USAGE", "CREATE"):
            (has,) = conn.execute(
                "SELECT has_schema_privilege(%s, %s, %s)", (role, APP_SCHEMA, priv)
            ).fetchone()  # type: ignore[misc]
            if has:
                violations.append(f"{role} has {priv} on schema {APP_SCHEMA}")

        # 3b. Table-like relations.
        for rel, priv in conn.execute(
            """
            SELECT c.relname, p.priv
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
                                    'REFERENCES','TRIGGER']) AS p(priv)
            WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','f')
              AND has_table_privilege(%s, c.oid, p.priv)
            ORDER BY 1, 2
            """,
            (APP_SCHEMA, role),
        ):
            violations.append(f"{role} has {priv} on {APP_SCHEMA}.{rel}")

        # 3b'. Column-level grants (not covered by has_table_privilege).
        for rel, priv in conn.execute(
            """
            SELECT c.relname, p.priv
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) AS p(priv)
            WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','f')
              AND has_any_column_privilege(%s, c.oid, p.priv)
              AND NOT has_table_privilege(%s, c.oid, p.priv)
            ORDER BY 1, 2
            """,
            (APP_SCHEMA, role, role),
        ):
            violations.append(f"{role} has column-level {priv} on {APP_SCHEMA}.{rel}")

        # 3c. Sequences.
        for rel, priv in conn.execute(
            """
            SELECT c.relname, p.priv
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['USAGE','SELECT','UPDATE']) AS p(priv)
            WHERE n.nspname = %s AND c.relkind = 'S'
              AND has_sequence_privilege(%s, c.oid, p.priv)
            ORDER BY 1, 2
            """,
            (APP_SCHEMA, role),
        ):
            violations.append(f"{role} has {priv} on sequence {APP_SCHEMA}.{rel}")

        # 3d. Functions: explicit grants to the role. (PostgreSQL's built-in EXECUTE-to-PUBLIC
        # default is unreachable for these roles without USAGE on the schema, checked in 3a.)
        for (fn,) in conn.execute(
            """
            SELECT DISTINCT p.proname
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE n.nspname = %s AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = %s)
            ORDER BY 1
            """,
            (APP_SCHEMA, role),
        ):
            violations.append(f"{role} has a grant on function {APP_SCHEMA}.{fn}")

    violations.extend(role_read_violations(conn))
    return violations


def probe_read(conn: psycopg.Connection, role: str, schema: str, table: str) -> str:
    """Read ``schema.table`` as ``role`` in a savepoint (rolled back, so the role and any
    error end with it). Returns ``"refused"`` (permission denied, SQLSTATE 42501),
    ``"read"`` (the query ran) or ``"error: <SQLSTATE>"`` (any other error)."""
    query = sql.SQL("SELECT 1 FROM {}.{} LIMIT 1").format(
        sql.Identifier(schema), sql.Identifier(table)
    )
    outcome = "read"
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            conn.execute(query)
    except psycopg.Error as exc:
        outcome = "refused" if exc.sqlstate == PERMISSION_DENIED else f"error: {exc.sqlstate}"
    return outcome


def role_read_violations(conn: psycopg.Connection) -> list[str]:
    """Read every relation in ``app`` (tables, views, materialized and foreign tables) as
    ``anon`` and ``authenticated``: each read must be refused with a permission error
    (42501); any other outcome is a violation naming it. Reads of relations in ``public``
    (and of a made-up ``public`` relation) must fail too."""
    violations: list[str] = []
    relations_sql = """
        SELECT n.nspname, c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s) AND c.relkind IN ('r','p','v','m','f')
        ORDER BY 1, 2
    """
    app_relations = [r for (_, r) in conn.execute(relations_sql, ([APP_SCHEMA],))]
    public_relations = list(conn.execute(relations_sql, (list(EXPOSED_SCHEMAS),)))
    for role in API_ROLES:
        # SET ROLE needs superuser or membership; without it nothing can be probed.
        if probe_read(conn, role, "pg_catalog", "pg_class") != "read":
            violations.append(
                f"cannot SET ROLE {role} to probe reads (connect as a superuser or a member "
                f"of {role})"
            )
            continue
        for rel in app_relations:
            outcome = probe_read(conn, role, APP_SCHEMA, rel)
            if outcome != "refused":
                violations.append(f"{role} reading {APP_SCHEMA}.{rel} was not refused ({outcome})")
        for nsp, rel in [*public_relations, ("public", PROBE_RELATION)]:
            outcome = probe_read(conn, role, nsp, rel)
            if outcome == "read":
                violations.append(f"{role} can read {nsp}.{rel}")
    return violations


def main() -> int:
    url = to_libpq_url(get_settings().sqlalchemy_url)
    try:
        with psycopg.connect(url) as conn:
            violations = find_violations(conn)
    except psycopg.OperationalError as exc:
        print(f"exposed-schema check: cannot connect to the database: {exc}", file=sys.stderr)
        return 1
    if violations:
        print("exposed-schema check FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print("exposed-schema check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
