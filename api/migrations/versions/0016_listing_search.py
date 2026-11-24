"""Listing keyword search: ``pg_trgm`` and a trigram index (FR-13, NFR-2, Story 2.6).

* Enables ``pg_trgm`` where Supabase puts extensions (schema ``extensions``) or, on plain
  Postgres (CI), in ``public``, like 0003 does for ``pg_net``. An already-enabled extension
  stays where it is. The search queries look up the extension's schema at run time, so they
  work wherever it lives. Extension-owned functions are allowed by the exposed-schema check.
* Adds ``listings.title_company``: a stored generated column
  ``lower(title || ' ' || company)`` (immutable, so Postgres backfills it automatically) with a
  GIN trigram index. Skills are covered by full-text search (``search_doc``) only, because a
  trigram expression over an array is not immutable.

``listings_search_doc_idx`` (full-text) already exists (0011).

Revision ID: 0016_listing_search
Revises: 0015_listing_maintenance
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_listing_search"
down_revision: str | None = "0015_listing_maintenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $do$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
                IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
                    CREATE EXTENSION pg_trgm WITH SCHEMA extensions;
                ELSE
                    CREATE EXTENSION pg_trgm WITH SCHEMA public;
                END IF;
            END IF;
        END
        $do$
        """
    )
    op.execute(
        "ALTER TABLE app.listings ADD COLUMN title_company text "
        "GENERATED ALWAYS AS (lower(title || ' ' || company)) STORED"
    )
    # The operator class is schema-qualified because the migration's search_path is `app`.
    op.execute(
        """
        DO $do$
        DECLARE
            trgm_schema text;
        BEGIN
            SELECT n.nspname INTO trgm_schema
            FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'pg_trgm';
            EXECUTE format(
                'CREATE INDEX listings_title_company_trgm_idx ON app.listings '
                'USING gin (title_company %I.gin_trgm_ops)',
                trgm_schema
            );
        END
        $do$
        """
    )


def downgrade() -> None:
    # The extension stays enabled (other things may use it); only our objects are removed.
    op.execute("DROP INDEX IF EXISTS app.listings_title_company_trgm_idx")
    op.execute("ALTER TABLE app.listings DROP COLUMN IF EXISTS title_company")
