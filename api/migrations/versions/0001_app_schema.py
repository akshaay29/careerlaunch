"""Create and lock down schema app (AD-1, AD-13).

Revision ID: 0001_app_schema
Revises:
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_app_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPOSED = "public, anon, authenticated"


def upgrade() -> None:
    # env.py may already have created it to host alembic_version.
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute(f"REVOKE ALL ON SCHEMA app FROM {EXPOSED}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON TABLES FROM {EXPOSED}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON SEQUENCES FROM {EXPOSED}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON FUNCTIONS FROM {EXPOSED}"
    )
    # Alembic's own table lives in `app` too, so it must satisfy the exposed-schema
    # check. RLS is enabled but NOT forced: the owning migration role stays exempt.
    op.execute("ALTER TABLE app.alembic_version ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON TABLE app.alembic_version FROM {EXPOSED}")


def downgrade() -> None:
    # Schema `app` is NOT dropped: it hosts alembic_version, which Alembic updates
    # after this function returns. Privilege revocations are intentionally kept
    # (re-granting to anon/authenticated would be unsafe), and RLS stays on
    # alembic_version so a downgraded DB still passes the exposed-schema check.
    pass
