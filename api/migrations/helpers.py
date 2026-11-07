"""Shared helpers for migrations. Import from a migration as ``from migrations.helpers import ...``."""

from alembic import op

from app.core.db import APP_SCHEMA

EXPOSED_ROLES = "public, anon, authenticated"


def app_table_rls_sql(table: str, schema: str = APP_SCHEMA) -> list[str]:
    """SQL that enables and forces RLS on ``schema.table`` and strips exposed-role grants."""
    qualified = f'{schema}."{table}"'
    return [
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
        f"REVOKE ALL ON TABLE {qualified} FROM {EXPOSED_ROLES}",
    ]


def create_app_table_rls(table: str, schema: str = APP_SCHEMA) -> None:
    """Call right after creating every table in ``app`` (AD-1): enable + force RLS."""
    for statement in app_table_rls_sql(table, schema):
        op.execute(statement)
