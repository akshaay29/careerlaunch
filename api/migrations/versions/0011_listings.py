"""Shared Listing pool (FR-6, FR-7, FR-8, AD-3, AD-7).

* ``sources`` (global, owned by ``listings``): one row per Source. ``enabled`` gates
  collection per Source. Seeded with ``linkedin`` and ``manual``.
* ``listings`` (global, owned by ``listings``, NO ``owner_id``): one row per
  ``(source, source_job_id)``. Pool rows keep only the minimal fields: title, company,
  location, job type, dates, source URL, skills (with ``vocab_version``) and ``search_doc``
  (lexemes only). No column holds the description.
* Visibility: ``visibility_owner_id`` NULL means the shared pool; otherwise only that
  Student sees the row (collected without ``pool_contribution`` consent). It references
  ``accounts`` ON DELETE CASCADE, so a deleted Student's private rows go with them.
* ``collected_by`` references ``accounts`` ON DELETE SET NULL: pool rows survive the
  collector's deletion with no Student reference.
* ``pin_count``, ``relevance`` and ``expired_at`` are hooks for Epics 3-4 and Story 2.5;
  browsing already hides expired rows.

Revision ID: 0011_listings
Revises: 0010_deleted_accounts
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0011_listings"
down_revision: str | None = "0010_deleted_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.sources (
            id text PRIMARY KEY,
            label text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT sources_id_check CHECK (id ~ '^[a-z][a-z0-9_]{0,31}$'),
            CONSTRAINT sources_label_check CHECK (char_length(label) BETWEEN 1 AND 100)
        )
        """
    )
    create_app_table_rls("sources")
    op.execute(
        """
        INSERT INTO app.sources (id, label, enabled)
        VALUES ('linkedin', 'LinkedIn', true), ('manual', 'Manual', true)
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE app.listings (
            id uuid PRIMARY KEY,
            source text NOT NULL,
            source_job_id text NOT NULL,
            url text NOT NULL,
            title text NOT NULL,
            company text NOT NULL DEFAULT '',
            location text NOT NULL DEFAULT '',
            job_type text,
            posted_at timestamptz,
            deadline date,
            skills text[] NOT NULL DEFAULT '{}',
            vocab_version text NOT NULL,
            search_doc tsvector NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            collected_by uuid,
            visibility_owner_id uuid,
            pin_count integer NOT NULL DEFAULT 0,
            relevance real NOT NULL DEFAULT 0,
            expired_at timestamptz,
            CONSTRAINT listings_source_fkey FOREIGN KEY (source)
                REFERENCES app.sources (id) ON DELETE RESTRICT,
            CONSTRAINT listings_collected_by_fkey FOREIGN KEY (collected_by)
                REFERENCES app.accounts (id) ON DELETE SET NULL,
            CONSTRAINT listings_visibility_owner_id_fkey FOREIGN KEY (visibility_owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT listings_source_job_key UNIQUE (source, source_job_id),
            CONSTRAINT listings_source_job_id_check
                CHECK (char_length(source_job_id) BETWEEN 1 AND 200),
            CONSTRAINT listings_url_check
                CHECK (char_length(url) <= 2048 AND url ~* '^https?://'),
            CONSTRAINT listings_title_check CHECK (char_length(title) BETWEEN 1 AND 300),
            CONSTRAINT listings_company_check CHECK (char_length(company) <= 200),
            CONSTRAINT listings_location_check CHECK (char_length(location) <= 200),
            CONSTRAINT listings_job_type_check
                CHECK (job_type IS NULL OR job_type IN ('internship', 'full_time')),
            CONSTRAINT listings_pin_count_check CHECK (pin_count >= 0)
        )
        """
    )
    op.execute("CREATE INDEX listings_search_doc_idx ON app.listings USING gin (search_doc)")
    op.execute(
        "CREATE INDEX listings_browse_idx ON app.listings (first_seen_at DESC, id DESC) "
        "WHERE expired_at IS NULL"
    )
    op.execute(
        "CREATE INDEX listings_visibility_owner_id_idx ON app.listings (visibility_owner_id)"
    )
    op.execute("CREATE INDEX listings_collected_by_idx ON app.listings (collected_by)")
    create_app_table_rls("listings")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.listings")
    op.execute("DROP TABLE IF EXISTS app.sources")
