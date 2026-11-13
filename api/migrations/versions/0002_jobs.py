"""Durable jobs (AD-4, AD-18): Procrastinate schema, job_outbox, schedules, ops_events, drain_state.

Procrastinate's bundled schema SQL (procrastinate 3.10.0) is installed into schema ``app``
with ``search_path=app``. Every table in ``app`` then gets RLS + revokes (AD-1).

Revision ID: 0002_jobs
Revises: 0001_app_schema
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

from migrations.helpers import create_app_table_rls

revision: str = "0002_jobs"
down_revision: str | None = "0001_app_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("job_outbox", "schedules", "ops_events", "drain_state")


def _app_tables() -> list[str]:
    rows = op.get_bind().exec_driver_sql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version' "
        "ORDER BY 1"
    )
    return [r[0] for r in rows]


def upgrade() -> None:
    # Vendor SQL, unmodified. SET LOCAL keeps search_path for this migration's transaction only.
    op.execute("SET LOCAL search_path TO app")
    op.get_bind().exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))
    op.execute("SET LOCAL search_path TO app")

    op.execute(
        """
        CREATE TABLE app.job_outbox (
            id uuid PRIMARY KEY,
            task text NOT NULL,
            key text NOT NULL,
            queue text NOT NULL DEFAULT 'default',
            run_at timestamptz,
            args jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(args) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX job_outbox_created_at_idx ON app.job_outbox (created_at, id)")

    op.execute(
        """
        CREATE TABLE app.schedules (
            id uuid PRIMARY KEY,
            kind text NOT NULL,
            owner_id uuid,
            next_run_at timestamptz NOT NULL,
            paused boolean NOT NULL DEFAULT false,
            "interval" interval NOT NULL CHECK ("interval" > interval '0'),
            CONSTRAINT schedules_kind_owner_key UNIQUE NULLS NOT DISTINCT (kind, owner_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX schedules_due_idx ON app.schedules (next_run_at) WHERE NOT paused"
    )

    op.execute(
        """
        CREATE TABLE app.ops_events (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            kind text NOT NULL,
            owner_id uuid,
            entity_type text,
            entity_id uuid,
            counts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(counts) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ops_events_created_at_idx ON app.ops_events (created_at)")
    op.execute("CREATE INDEX ops_events_kind_created_at_idx ON app.ops_events (kind, created_at)")
    # Append-only (AD-18): rows may be inserted and (by the 90-day purge) deleted, never updated.
    op.execute(
        """
        CREATE FUNCTION app.ops_events_block_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'ops_events is append-only' USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ops_events_no_update BEFORE UPDATE ON app.ops_events
        FOR EACH ROW EXECUTE FUNCTION app.ops_events_block_update()
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app.ops_events_block_update() FROM PUBLIC")

    op.execute(
        """
        CREATE TABLE app.drain_state (
            id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            last_drain_at timestamptz
        )
        """
    )

    # AD-1: RLS + revokes on every table in `app` (Procrastinate's included).
    for table in _app_tables():
        create_app_table_rls(table)
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA app FROM public, anon, authenticated")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM public, anon, authenticated")


def downgrade() -> None:
    for table in NEW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS app.{table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app.ops_events_block_update()")
    # Everything Procrastinate's schema SQL created: tables (with their triggers and
    # sequences), then functions, then types.
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'app' AND c.relkind = 'r' AND c.relname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS app.%I CASCADE', r.relname);
            END LOOP;
            FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                     JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'app' AND p.proname LIKE 'procrastinate%'
            LOOP
                EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
            END LOOP;
            FOR r IN SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                     WHERE n.nspname = 'app' AND t.typname LIKE 'procrastinate%' AND t.typtype IN ('e', 'c')
            LOOP
                EXECUTE format('DROP TYPE IF EXISTS app.%I CASCADE', r.typname);
            END LOOP;
        END
        $$
        """
    )
