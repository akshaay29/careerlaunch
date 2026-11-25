"""Retention: the daily ``core.purge_history`` schedule (AD-17, Story 4.4).

One global (``owner_id`` NULL) daily ``schedules`` row runs ``core.purge_history`` at about
03:30 UTC, after the 03:00-03:15 Listing maintenance jobs (0015). The task deletes finished
background jobs and leaked outbox rows after 7 days and ``ops_events`` after 90 days
(``app.core.retention``). No table changes.

Revision ID: 0021_retention
Revises: 0020_manual_listings
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_retention"
down_revision: str | None = "0020_manual_listings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION_TASK = "core.purge_history"
RETENTION_SCHEDULE_ID = "0193a6c0-0000-7000-8000-000000000021"
# Minutes after 00:00 UTC.
RETENTION_MINUTE_OF_DAY = 3 * 60 + 30


def upgrade() -> None:
    # The next 03:30 UTC that is still in the future.
    op.execute(
        f"""
        INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval")
        SELECT '{RETENTION_SCHEDULE_ID}', '{RETENTION_TASK}', NULL,
               CASE WHEN t.slot > now() THEN t.slot ELSE t.slot + interval '24 hours' END,
               interval '24 hours'
        FROM (
            SELECT (date_trunc('day', now() AT TIME ZONE 'UTC')
                    + interval '{RETENTION_MINUTE_OF_DAY} minutes') AT TIME ZONE 'UTC' AS slot
        ) t
        ON CONFLICT ON CONSTRAINT schedules_kind_owner_key DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM app.schedules WHERE kind = '{RETENTION_TASK}' AND owner_id IS NULL")
