"""Applications and their Status history (FR-16, AD-7, Story 4.1). Both tables are owned by
``tracker`` and have forced RLS.

* ``applications``: one saved Listing per Student and Listing (partial unique on
  ``(owner_id, listing_id) WHERE listing_id IS NOT NULL``). ``status`` is written only by
  ``tracker.service.transition``. ``title``, ``company``, ``url`` and ``deadline`` are a
  snapshot of the Listing taken at save, read when the Listing is gone. CASCADE from
  ``accounts``; SET NULL from ``listings`` (purge, eviction and takedown delete Listings) and
  from ``resumes`` (a deleted Resume version clears the choice).
* ``application_status_history``: one row per Status change, written in the transition's
  transaction; CASCADE from ``applications`` and ``accounts``.

Revision ID: 0018_applications
Revises: 0017_match_scores
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0018_applications"
down_revision: str | None = "0017_match_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "'saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE app.applications (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            listing_id uuid,
            status text NOT NULL,
            notes text,
            applied_on date,
            resume_id uuid,
            title text NOT NULL,
            company text NOT NULL DEFAULT '',
            url text NOT NULL,
            deadline date,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT applications_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT applications_listing_id_fkey FOREIGN KEY (listing_id)
                REFERENCES app.listings (id) ON DELETE SET NULL,
            CONSTRAINT applications_resume_id_fkey FOREIGN KEY (resume_id)
                REFERENCES app.resumes (id) ON DELETE SET NULL,
            CONSTRAINT applications_status_check CHECK (status IN ({STATUSES})),
            CONSTRAINT applications_notes_check CHECK (char_length(notes) <= 4000)
        )
        """
    )
    create_app_table_rls("applications")
    op.execute(
        "CREATE UNIQUE INDEX applications_owner_listing_key ON app.applications "
        "(owner_id, listing_id) WHERE listing_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX applications_owner_updated_idx ON app.applications "
        "(owner_id, updated_at DESC)"
    )
    # The FK lookups of ON DELETE SET NULL (Listing purge, Resume deletion).
    op.execute("CREATE INDEX applications_listing_id_idx ON app.applications (listing_id)")
    op.execute("CREATE INDEX applications_resume_id_idx ON app.applications (resume_id)")

    op.execute(
        f"""
        CREATE TABLE app.application_status_history (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL,
            owner_id uuid NOT NULL,
            from_status text,
            to_status text NOT NULL,
            changed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT application_status_history_application_id_fkey
                FOREIGN KEY (application_id)
                REFERENCES app.applications (id) ON DELETE CASCADE,
            CONSTRAINT application_status_history_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT application_status_history_from_check
                CHECK (from_status IS NULL OR from_status IN ({STATUSES})),
            CONSTRAINT application_status_history_to_check CHECK (to_status IN ({STATUSES})),
            CONSTRAINT application_status_history_change_check
                CHECK (from_status IS DISTINCT FROM to_status)
        )
        """
    )
    create_app_table_rls("application_status_history")
    op.execute(
        "CREATE INDEX application_status_history_app_changed_idx "
        "ON app.application_status_history (application_id, changed_at DESC)"
    )
    op.execute(
        "CREATE INDEX application_status_history_owner_idx "
        "ON app.application_status_history (owner_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.application_status_history")
    op.execute("DROP TABLE IF EXISTS app.applications")
