"""Consent records per purpose (AD-11, FR-2).

* ``consents`` is owned by ``accounts``. One row per grant: granting inserts a row, revoking
  sets ``revoked_at`` on the active row, history is kept.
* At most one active (``revoked_at IS NULL``) row per ``(owner_id, purpose)``: partial unique
  index ``consents_one_active_idx``.
* ``end_reason`` says why a row ended: ``revoked`` (the Student withdrew) or ``superseded``
  (replaced by a grant on a newer text version). It is set exactly when ``revoked_at`` is.
* ``purpose`` is limited to the Phase 1 purposes by a CHECK constraint.
* ``owner_id`` references ``accounts(id) ON DELETE CASCADE``.

Revision ID: 0005_consents
Revises: 0004_accounts
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0005_consents"
down_revision: str | None = "0004_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.consents (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            purpose text NOT NULL,
            version text NOT NULL,
            granted_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz,
            end_reason text,
            CONSTRAINT consents_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT consents_purpose_check
                CHECK (purpose IN ('provider_runs', 'pool_contribution')),
            CONSTRAINT consents_version_check CHECK (version <> ''),
            CONSTRAINT consents_revoked_after_granted_check
                CHECK (revoked_at IS NULL OR revoked_at >= granted_at),
            CONSTRAINT consents_end_reason_check
                CHECK (end_reason IN ('revoked', 'superseded')),
            CONSTRAINT consents_end_reason_matches_revoked_at_check
                CHECK ((revoked_at IS NULL) = (end_reason IS NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX consents_one_active_idx ON app.consents (owner_id, purpose) "
        "WHERE revoked_at IS NULL"
    )
    op.execute("CREATE INDEX consents_owner_id_idx ON app.consents (owner_id)")
    create_app_table_rls("consents")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.consents")
