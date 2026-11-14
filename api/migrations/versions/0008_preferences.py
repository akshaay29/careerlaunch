"""Preferences (FR-5, AD-9, AD-21, Time convention).

* ``preferences`` is owned by ``profile``: one row per owner (``owner_id`` is the primary key,
  ``ON DELETE CASCADE`` from ``accounts``).
* No row means the defaults (threshold 60, ``daily`` alerts, ``Asia/Kolkata``, empty lists,
  eligibility not stated).
* CHECK constraints back the structural rules (AD-13): threshold 0–100, list sizes and item
  lengths, the job type / alert frequency / work authorisation / degree enums, batch year and
  CGPA ranges, and ``degree_other`` only (and non-blank) with ``degree = 'other'``. The API
  alone normalises text (trim, dedupe, control characters) and validates the time zone as
  an IANA name; the DB only bounds its length.
* ``degree`` holds a canonical id from ``app.core.degrees``. The CHECK lists the ids as they
  are at this revision; a test keeps it in step with the module, and a new id needs a new
  migration that replaces the constraint.
* ``app.text_items_within(arr, max_len)`` is an immutable helper for the per-item length
  checks (CHECK constraints cannot hold subqueries). It is in schema ``app``, so it is not
  reachable through the Data API, and EXECUTE is revoked from the exposed roles anyway.

Revision ID: 0008_preferences
Revises: 0007_skill_profiles
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import EXPOSED_ROLES, create_app_table_rls

revision: str = "0008_preferences"
down_revision: str | None = "0007_skill_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of app.core.degrees.DEGREE_IDS at this revision (see the docstring).
DEGREE_IDS = (
    "btech", "mtech", "bsc", "msc", "bca", "mca", "bba", "mba", "pgdm",
    "bcom", "mcom", "ba", "ma", "barch", "bdes", "bpharm", "mpharm",
    "mbbs", "bds", "llb", "llm", "integrated", "phd", "diploma", "other",
)  # fmt: skip


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app.text_items_within(arr text[], max_len int) RETURNS boolean
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog
        AS $$
            SELECT coalesce(bool_and(x IS NOT NULL AND char_length(x) BETWEEN 1 AND max_len), true)
            FROM unnest(arr) AS x
        $$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION app.text_items_within(text[], int) FROM {EXPOSED_ROLES}"
    )
    degree_list = ", ".join(f"'{d}'" for d in DEGREE_IDS)
    op.execute(
        f"""
        CREATE TABLE app.preferences (
            owner_id uuid PRIMARY KEY,
            role_titles text[] NOT NULL DEFAULT '{{}}',
            locations text[] NOT NULL DEFAULT '{{}}',
            job_types text[] NOT NULL DEFAULT '{{}}',
            batch_year int,
            cgpa numeric(4, 2),
            degree text,
            degree_other text,
            work_authorisation text,
            threshold smallint NOT NULL DEFAULT 60,
            alert_frequency text NOT NULL DEFAULT 'daily',
            timezone text NOT NULL DEFAULT 'Asia/Kolkata',
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT preferences_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT preferences_role_titles_check
                CHECK (cardinality(role_titles) <= 10
                       AND app.text_items_within(role_titles, 80)),
            CONSTRAINT preferences_locations_check
                CHECK (cardinality(locations) <= 10
                       AND app.text_items_within(locations, 80)),
            CONSTRAINT preferences_job_types_check
                CHECK (job_types <@ ARRAY['internship', 'full_time']::text[]
                       AND array_position(job_types, NULL) IS NULL),
            CONSTRAINT preferences_batch_year_check
                CHECK (batch_year BETWEEN 2000 AND 2100),
            CONSTRAINT preferences_cgpa_check
                CHECK (cgpa BETWEEN 0 AND 10),
            CONSTRAINT preferences_degree_check
                CHECK (degree IN ({degree_list})),
            CONSTRAINT preferences_degree_other_check
                CHECK (degree_other IS NULL
                       OR (degree = 'other' AND btrim(degree_other) <> ''
                           AND char_length(degree_other) <= 80)),
            CONSTRAINT preferences_work_authorisation_check
                CHECK (work_authorisation IN ('india_authorised', 'needs_sponsorship')),
            CONSTRAINT preferences_threshold_check
                CHECK (threshold BETWEEN 0 AND 100),
            CONSTRAINT preferences_alert_frequency_check
                CHECK (alert_frequency IN ('daily', 'weekly', 'off')),
            CONSTRAINT preferences_timezone_check
                CHECK (char_length(timezone) BETWEEN 1 AND 64)
        )
        """
    )
    create_app_table_rls("preferences")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.preferences")
    op.execute("DROP FUNCTION IF EXISTS app.text_items_within(text[], int)")
