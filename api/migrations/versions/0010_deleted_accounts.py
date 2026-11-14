"""Deleted-account tombstones (FR-2, AD-17).

* ``deleted_accounts`` is global (owned by ``accounts``) and holds ONLY the Auth uid of a
  deleted account and when it was deleted: no email, no other personal data. The uid is a
  pseudonymous id that Supabase never reuses.
* No foreign key: the ``accounts`` row is gone by design.
* ``accounts.service.get_or_provision`` refuses a tombstoned id (401), so an access token
  issued before deletion cannot re-provision the account.
* ``files_purged_at`` / ``auth_deleted_at`` are set by the ``accounts.purge_files`` /
  ``accounts.delete_auth_user`` jobs when they succeed. A global daily ``schedules`` row runs
  ``accounts.resume_pending_deletions``, which re-enqueues the job(s) still missing, so a
  deletion whose jobs used up their retries is still finished.

Revision ID: 0010_deleted_accounts
Revises: 0009_documents
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0010_deleted_accounts"
down_revision: str | None = "0009_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESUME_TASK = "accounts.resume_pending_deletions"
RESUME_SCHEDULE_ID = "01936a00-0000-7000-8000-000000000a01"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.deleted_accounts (
            id uuid PRIMARY KEY,
            deleted_at timestamptz NOT NULL DEFAULT now(),
            files_purged_at timestamptz,
            auth_deleted_at timestamptz
        )
        """
    )
    create_app_table_rls("deleted_accounts")
    # Global (owner_id NULL) daily backstop; first run at the next UTC midnight.
    op.execute(
        f"""
        INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval")
        VALUES ('{RESUME_SCHEDULE_ID}', '{RESUME_TASK}', NULL,
                date_trunc('day', now()) + interval '1 day', interval '1 day')
        ON CONFLICT ON CONSTRAINT schedules_kind_owner_key DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM app.schedules WHERE kind = '{RESUME_TASK}' AND owner_id IS NULL")
    op.execute("DROP TABLE IF EXISTS app.deleted_accounts")
