"""pg_cron + pg_net call POST /internal/jobs/drain every 5 minutes (AD-4).

The URL and bearer secret are read at run time from Supabase Vault
(``vault.decrypted_secrets`` names ``drain_url`` and ``drain_secret``); nothing secret is in
this file. Where pg_cron, pg_net or the ``vault`` schema is unavailable (CI's plain Postgres),
the migration only raises a NOTICE and skips.

Revision ID: 0003_pg_cron_drain
Revises: 0002_jobs
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_pg_cron_drain"
down_revision: str | None = "0002_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_NAME = "careerlaunch-drain"
SCHEDULE = "*/5 * * * *"

# Runs inside pg_cron. net.http_post is asynchronous; pg_net waits up to 90 s for the
# response (the API answers 202 and drains after responding).
DRAIN_COMMAND = """
SELECT net.http_post(
    url := (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'drain_url'),
    headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'Authorization', 'Bearer ' || (
            SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'drain_secret'
        )
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 90000
);
""".strip()


def upgrade() -> None:
    op.execute(
        f"""
        DO $do$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron')
               OR NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_net')
               OR NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'vault') THEN
                RAISE NOTICE 'pg_cron, pg_net or vault unavailable: skipping the {JOB_NAME} schedule';
                RETURN;
            END IF;

            -- Supabase's documented placement for both extensions.
            CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
                CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;
            ELSE
                CREATE EXTENSION IF NOT EXISTS pg_net;
            END IF;

            -- cron.schedule upserts by job name, so re-running is safe.
            PERFORM cron.schedule('{JOB_NAME}', '{SCHEDULE}', $cmd${DRAIN_COMMAND}$cmd$);
        END
        $do$
        """
    )


def downgrade() -> None:
    # Extensions stay enabled (other things may use them); only our job is removed.
    op.execute(
        f"""
        DO $do$
        BEGIN
            -- Nested IFs: cron.job must not be planned when the cron schema is absent.
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'cron') THEN
                IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = '{JOB_NAME}') THEN
                    PERFORM cron.unschedule('{JOB_NAME}');
                END IF;
            END IF;
        END
        $do$
        """
    )
