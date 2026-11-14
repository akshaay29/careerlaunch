"""Documents (FR-26, Files convention).

* ``documents`` is owned by ``profile``. One row per stored Document (cover letter,
  transcript, certificate...). ``id`` (UUID v7, from the app) is also the Storage object id:
  the file lives at ``<owner_id>/document/<id>`` in the private bucket.
* ``owner_id`` references ``accounts(id) ON DELETE CASCADE``.
* ``filename`` is the sanitised original name (display and download name only).
* ``content_type`` is PDF, DOCX or ``text/plain`` (sniffed from the content by the api).

Revision ID: 0009_documents
Revises: 0008_preferences
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0009_documents"
down_revision: str | None = "0008_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.documents (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            filename text NOT NULL,
            content_type text NOT NULL,
            size_bytes integer NOT NULL,
            sha256 text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT documents_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT documents_filename_check
                CHECK (char_length(filename) BETWEEN 1 AND 255),
            CONSTRAINT documents_content_type_check CHECK (content_type IN (
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'text/plain'
            )),
            CONSTRAINT documents_size_bytes_check CHECK (size_bytes BETWEEN 1 AND 5242880),
            CONSTRAINT documents_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute("CREATE INDEX documents_owner_id_idx ON app.documents (owner_id, id DESC)")
    create_app_table_rls("documents")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.documents")
