"""Provider Keys (FR-28, AD-6, AD-12).

* ``provider_credentials`` (owned by ``accounts``): at most one row per ``(owner_id,
  provider)``. The token is stored ONLY as AES-GCM ciphertext from ``core.crypto``, whose
  additional authenticated data is ``provider_credentials:<id>:<owner_id>``, so a blob copied
  to another row or owner does not decrypt. ``key_version`` is the master-key version that
  sealed it (rotation). ``last4`` is the only plaintext fragment kept, for the masked hint.
* ``owner_id`` references ``accounts`` ON DELETE CASCADE: deleting the account removes it.

Revision ID: 0012_provider_credentials
Revises: 0011_listings
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0012_provider_credentials"
down_revision: str | None = "0011_listings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.provider_credentials (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL,
            provider text NOT NULL,
            ciphertext bytea NOT NULL,
            key_version smallint NOT NULL,
            last4 char(4) NOT NULL,
            provider_user_id text,
            validated_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT provider_credentials_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT provider_credentials_owner_provider_key UNIQUE (owner_id, provider),
            CONSTRAINT provider_credentials_provider_check CHECK (provider IN ('apify')),
            CONSTRAINT provider_credentials_key_version_check CHECK (key_version BETWEEN 1 AND 32767),
            CONSTRAINT provider_credentials_provider_user_id_check
                CHECK (provider_user_id IS NULL OR char_length(provider_user_id) <= 100)
        )
        """
    )
    create_app_table_rls("provider_credentials")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.provider_credentials")
