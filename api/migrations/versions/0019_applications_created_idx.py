"""Index the Tracker list order (Story 4.2).

The Tracker lists a Student's Applications newest saved first, keyed on
``(created_at, id)``. ``applications_owner_created_idx`` serves that order per owner.

Revision ID: 0019_applications_created_idx
Revises: 0018_applications
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_applications_created_idx"
down_revision: str | None = "0018_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX applications_owner_created_idx "
        "ON app.applications (owner_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.applications_owner_created_idx")
