"""Resume versions (FR-3, Files convention).

* ``resumes`` is owned by ``profile``. One row per uploaded version. ``id`` (UUID v7, from the
  app) is also the Storage object id: the file lives at ``<owner_id>/resume/<id>`` in the
  private bucket.
* At most one ``is_active`` row per owner: partial unique index ``resumes_one_active_idx``.
* ``owner_id`` references ``accounts(id) ON DELETE CASCADE``.
* ``filename`` is the sanitised original name (display and download name only).

Revision ID: 0006_resumes
Revises: 0005_consents
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0006_resumes"
down_revision: str | None = "0005_consents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.resumes (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            filename text NOT NULL,
            content_type text NOT NULL,
            size_bytes integer NOT NULL,
            sha256 text NOT NULL,
            is_active boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT resumes_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT resumes_filename_check
                CHECK (char_length(filename) BETWEEN 1 AND 255),
            CONSTRAINT resumes_content_type_check CHECK (content_type IN (
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )),
            CONSTRAINT resumes_size_bytes_check CHECK (size_bytes BETWEEN 1 AND 5242880),
            CONSTRAINT resumes_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX resumes_one_active_idx ON app.resumes (owner_id) WHERE is_active"
    )
    op.execute("CREATE INDEX resumes_owner_id_idx ON app.resumes (owner_id, id DESC)")
    create_app_table_rls("resumes")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.resumes")
