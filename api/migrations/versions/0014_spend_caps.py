"""Spend caps, run estimates and pause reasons (FR-28, AD-12, Story 2.4).

* ``spend_caps`` (owned by ``accounts``): a Student's per-run and monthly caps in USD. No row
  means the defaults (``COLLECT_MAX_CHARGE_USD`` per run, ``DEFAULT_MONTHLY_CAP_USD`` a
  month). Bounds: per-run 0.05..2.00, monthly 0.25..100 and monthly >= per-run.
  ``owner_id`` references ``accounts`` ON DELETE CASCADE. RLS.
* ``provider_runs.estimated_cost_usd``: ``max_items x APIFY_PRICE_PER_ITEM_USD``, written on
  every new row (NULL on rows from before this migration).
* ``provider_status.reason`` is one of the four pause reasons (or NULL).

Revision ID: 0014_spend_caps
Revises: 0013_provider_runs
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0014_spend_caps"
down_revision: str | None = "0013_provider_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.spend_caps (
            owner_id uuid PRIMARY KEY,
            per_run_usd numeric(10, 4) NOT NULL,
            monthly_usd numeric(10, 4) NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT spend_caps_owner_id_fkey FOREIGN KEY (owner_id)
                REFERENCES app.accounts (id) ON DELETE CASCADE,
            CONSTRAINT spend_caps_per_run_check CHECK (per_run_usd BETWEEN 0.05 AND 2.00),
            CONSTRAINT spend_caps_monthly_check CHECK (monthly_usd BETWEEN 0.25 AND 100),
            CONSTRAINT spend_caps_order_check CHECK (monthly_usd >= per_run_usd)
        )
        """
    )
    create_app_table_rls("spend_caps")

    op.execute("ALTER TABLE app.provider_runs ADD COLUMN estimated_cost_usd numeric(10, 4)")
    op.execute(
        """
        ALTER TABLE app.provider_runs ADD CONSTRAINT provider_runs_estimate_check
            CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0)
        """
    )

    op.execute("ALTER TABLE app.provider_status DROP CONSTRAINT provider_status_reason_check")
    op.execute(
        """
        ALTER TABLE app.provider_status ADD CONSTRAINT provider_status_reason_check
            CHECK (reason IS NULL OR reason IN
                   ('key_invalid', 'credit_exhausted', 'not_permitted', 'account_blocked'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.provider_status DROP CONSTRAINT provider_status_reason_check")
    op.execute(
        """
        ALTER TABLE app.provider_status ADD CONSTRAINT provider_status_reason_check
            CHECK (reason IS NULL OR char_length(reason) <= 200)
        """
    )
    op.execute("ALTER TABLE app.provider_runs DROP CONSTRAINT provider_runs_estimate_check")
    op.execute("ALTER TABLE app.provider_runs DROP COLUMN estimated_cost_usd")
    op.execute("DROP TABLE IF EXISTS app.spend_caps")
