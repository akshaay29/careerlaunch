"""Manual Listings: jobs a Student adds from another site (FR-18, AD-7, Story 4.3).

* ``listings.url`` becomes nullable, but only a ``manual`` Listing may lack one
  (``listings_url_required_check``). The http(s) and length CHECK still applies when it is set.
* ``listings.private_description``: the optional description a Student typed for their own
  manual Listing (at most 4,000 characters). Only ``manual`` Listings may have one, so pool
  Listings stay description-free (AD-7).
* ``applications.url`` (the snapshot) becomes nullable: a manual Listing added by title only
  has no URL to snapshot.

Revision ID: 0020_manual_listings
Revises: 0019_applications_created_idx
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_manual_listings"
down_revision: str | None = "0019_applications_created_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.listings ALTER COLUMN url DROP NOT NULL")
    op.execute("ALTER TABLE app.listings DROP CONSTRAINT listings_url_check")
    op.execute(
        "ALTER TABLE app.listings ADD CONSTRAINT listings_url_check "
        "CHECK (url IS NULL OR (char_length(url) <= 2048 AND url ~* '^https?://'))"
    )
    op.execute(
        "ALTER TABLE app.listings ADD CONSTRAINT listings_url_required_check "
        "CHECK (url IS NOT NULL OR source = 'manual')"
    )
    op.execute("ALTER TABLE app.listings ADD COLUMN private_description text")
    op.execute(
        "ALTER TABLE app.listings ADD CONSTRAINT listings_private_description_manual_check "
        "CHECK (private_description IS NULL OR source = 'manual')"
    )
    op.execute(
        "ALTER TABLE app.listings ADD CONSTRAINT listings_private_description_length_check "
        "CHECK (char_length(private_description) <= 4000)"
    )
    op.execute("ALTER TABLE app.applications ALTER COLUMN url DROP NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE app.applications SET url = '' WHERE url IS NULL")
    op.execute("ALTER TABLE app.applications ALTER COLUMN url SET NOT NULL")
    op.execute(
        "ALTER TABLE app.listings DROP CONSTRAINT IF EXISTS "
        "listings_private_description_length_check"
    )
    op.execute(
        "ALTER TABLE app.listings DROP CONSTRAINT IF EXISTS "
        "listings_private_description_manual_check"
    )
    op.execute("ALTER TABLE app.listings DROP COLUMN IF EXISTS private_description")
    # Manual Listings added without a URL cannot survive the old NOT NULL column.
    op.execute("DELETE FROM app.listings WHERE url IS NULL")
    op.execute("ALTER TABLE app.listings DROP CONSTRAINT IF EXISTS listings_url_required_check")
    op.execute("ALTER TABLE app.listings DROP CONSTRAINT listings_url_check")
    op.execute(
        "ALTER TABLE app.listings ADD CONSTRAINT listings_url_check "
        "CHECK (char_length(url) <= 2048 AND url ~* '^https?://')"
    )
    op.execute("ALTER TABLE app.listings ALTER COLUMN url SET NOT NULL")
