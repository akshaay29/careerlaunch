"""Listing maintenance: takedown blocks, daily jobs, indexes (FR-8, NFR-7, AD-7, Story 2.5).

* ``listing_blocks`` (owned by ``listings``): a global table (no ``owner_id``) of takedowns.
  ``kind`` is ``listing`` (``source`` set, ``value`` the ``source_job_id``), ``url`` (the
  canonical URL: no query or fragment, lower-cased host) or ``company`` (trimmed, case-folded);
  ``source`` is NULL for the other two. Unique on ``(kind, source, value)``. RLS.
* Four global (``owner_id`` NULL) daily ``schedules`` rows, staggered by 5 minutes from about
  03:00 UTC, like the 0013 reaper: ``listings.expire``, ``listings.purge``, ``listings.evict``
  and ``listings.quota_check``.
* Two partial indexes: ``expired_at`` where set (the purge scan) and ``(relevance,
  last_seen_at)`` where the Listing is active and unpinned (the eviction scan).

``pin_count``, ``relevance`` and ``expired_at`` already exist (0011).

Revision ID: 0015_listing_maintenance
Revises: 0014_spend_caps
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0015_listing_maintenance"
down_revision: str | None = "0014_spend_caps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (task, schedule id, minutes after 03:00 UTC)
MAINTENANCE_SCHEDULES = (
    ("listings.expire", "0193a6c0-0000-7000-8000-000000000015", 0),
    ("listings.purge", "0193a6c0-0000-7000-8000-000000000016", 5),
    ("listings.evict", "0193a6c0-0000-7000-8000-000000000017", 10),
    ("listings.quota_check", "0193a6c0-0000-7000-8000-000000000018", 15),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.listing_blocks (
            id uuid PRIMARY KEY,
            kind text NOT NULL,
            source text,
            value text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT listing_blocks_kind_check CHECK (kind IN ('listing', 'url', 'company')),
            CONSTRAINT listing_blocks_source_check
                CHECK ((kind = 'listing') = (source IS NOT NULL)),
            CONSTRAINT listing_blocks_value_check
                CHECK (char_length(value) BETWEEN 1 AND 2048),
            CONSTRAINT listing_blocks_kind_source_value_key
                UNIQUE NULLS NOT DISTINCT (kind, source, value)
        )
        """
    )
    create_app_table_rls("listing_blocks")

    op.execute(
        "CREATE INDEX listings_expired_at_idx ON app.listings (expired_at) "
        "WHERE expired_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX listings_evict_idx ON app.listings (relevance, last_seen_at) "
        "WHERE expired_at IS NULL AND pin_count = 0"
    )

    for task, schedule_id, minutes in MAINTENANCE_SCHEDULES:
        # The next 03:00 UTC (plus the stagger) that is still in the future.
        op.execute(
            f"""
            INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval")
            SELECT '{schedule_id}', '{task}', NULL,
                   CASE WHEN t.slot > now() THEN t.slot ELSE t.slot + interval '24 hours' END,
                   interval '24 hours'
            FROM (
                SELECT (date_trunc('day', now() AT TIME ZONE 'UTC')
                        + interval '3 hours' + interval '{minutes} minutes')
                       AT TIME ZONE 'UTC' AS slot
            ) t
            ON CONFLICT ON CONSTRAINT schedules_kind_owner_key DO NOTHING
            """
        )


def downgrade() -> None:
    for task, _, _ in MAINTENANCE_SCHEDULES:
        op.execute(f"DELETE FROM app.schedules WHERE kind = '{task}' AND owner_id IS NULL")
    op.execute("DROP INDEX IF EXISTS app.listings_evict_idx")
    op.execute("DROP INDEX IF EXISTS app.listings_expired_at_idx")
    op.execute("DROP TABLE IF EXISTS app.listing_blocks")
