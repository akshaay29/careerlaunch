"""Provider runs and provider status (FR-6, AD-12, Story 2.3).

* ``provider_status`` (owned by ``accounts``): one row per Student whose collection is not
  plainly active. No row means ``active``. ``paused`` carries a ``reason``;
  ``exhausted_until`` (credit exhausted) blocks runs until that time. Story 2.4 writes it.
* ``provider_runs`` (owned by ``listings``): one row per attempted Apify run, written only by
  ``listings.service.start_provider_run`` and the poll of that run. ``run_key`` is unique (one
  run per Student per local day, or ``seed:<uuid>``); ``apify_run_id`` is unique once set.
  ``owner_id`` is NULL for the System seed run. Holds ids, caps, counts and cost only:
  never the token or a raw item.
* Both ``owner_id`` columns reference ``accounts`` ON DELETE CASCADE.
* A global (``owner_id`` NULL) hourly ``schedules`` row runs ``listings.reap_stale_runs``,
  which fails runs stuck in ``starting`` or ``running``.

Revision ID: 0013_provider_runs
Revises: 0012_provider_credentials
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0013_provider_runs"
down_revision: str | None = "0012_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REAP_TASK = "listings.reap_stale_runs"
REAP_SCHEDULE_ID = "0193a6c0-0000-7000-8000-000000000013"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.provider_status (
            owner_id uuid PRIMARY KEY,
            status text NOT NULL DEFAULT 'active',
            reason text,
            exhausted_until timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT provider_status_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT provider_status_status_check CHECK (status IN ('active', 'paused')),
            CONSTRAINT provider_status_reason_check
                CHECK (reason IS NULL OR char_length(reason) <= 200)
        )
        """
    )
    create_app_table_rls("provider_status")

    op.execute(
        """
        CREATE TABLE app.provider_runs (
            id uuid PRIMARY KEY,
            owner_id uuid,
            purpose text NOT NULL,
            run_key text NOT NULL,
            actor text,
            status text NOT NULL,
            apify_run_id text,
            dataset_id text,
            params jsonb NOT NULL DEFAULT '{}'::jsonb,
            max_items integer NOT NULL,
            max_charge_usd numeric(10, 4) NOT NULL,
            cost_usd numeric(12, 6),
            items integer,
            ingested integer,
            rejected integer,
            duplicates integer,
            not_visible integer,
            error_kind text,
            poll_attempts integer NOT NULL DEFAULT 0,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT provider_runs_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT provider_runs_run_key_key UNIQUE (run_key),
            CONSTRAINT provider_runs_apify_run_id_key UNIQUE (apify_run_id),
            CONSTRAINT provider_runs_purpose_check CHECK (purpose IN ('jobs')),
            CONSTRAINT provider_runs_status_check
                CHECK (status IN ('starting', 'running', 'succeeded', 'failed', 'refused')),
            CONSTRAINT provider_runs_run_key_check CHECK (char_length(run_key) <= 200),
            CONSTRAINT provider_runs_error_kind_check
                CHECK (error_kind IS NULL OR error_kind ~ '^[a-z][a-z0-9_]{0,39}$'),
            CONSTRAINT provider_runs_running_has_run_id_check
                CHECK (status NOT IN ('running', 'succeeded') OR apify_run_id IS NOT NULL),
            CONSTRAINT provider_runs_caps_check
                CHECK (max_items > 0 AND max_charge_usd > 0),
            CONSTRAINT provider_runs_counts_check CHECK (
                poll_attempts >= 0
                AND (items IS NULL OR items >= 0)
                AND (ingested IS NULL OR ingested >= 0)
                AND (rejected IS NULL OR rejected >= 0)
                AND (duplicates IS NULL OR duplicates >= 0)
                AND (not_visible IS NULL OR not_visible >= 0)
                AND (cost_usd IS NULL OR cost_usd >= 0)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX provider_runs_owner_created_idx ON app.provider_runs (owner_id, created_at)"
    )
    create_app_table_rls("provider_runs")
    op.execute(
        f"""
        INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval")
        VALUES ('{REAP_SCHEDULE_ID}', '{REAP_TASK}', NULL,
                date_trunc('hour', now()) + interval '1 hour', interval '1 hour')
        ON CONFLICT ON CONSTRAINT schedules_kind_owner_key DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM app.schedules WHERE kind = '{REAP_TASK}' AND owner_id IS NULL")
    op.execute("DROP TABLE IF EXISTS app.provider_runs")
    op.execute("DROP TABLE IF EXISTS app.provider_status")
