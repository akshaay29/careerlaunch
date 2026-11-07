"""AD-1 CI guardrail: nothing reachable through the Supabase Data API.

Exits 1 if:
  * any relation (table, view, sequence, index, ...) exists in ``public`` or ``graphql_public``;
  * any table in schema ``app`` does not have row level security enabled;
  * ``anon`` or ``authenticated`` hold any privilege on schema ``app`` or its objects
    (directly, via role membership, or via PUBLIC).

Usage: ``uv run python scripts/check_exposed_schema.py`` (reads DATABASE_URL via Settings).
"""

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings, to_libpq_url  # noqa: E402

EXPOSED_SCHEMAS = ("public", "graphql_public")
APP_SCHEMA = "app"
API_ROLES = ("anon", "authenticated")

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
