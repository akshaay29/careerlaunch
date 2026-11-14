"""Accounts, insert-only admin_audit, and account foreign keys (AD-5, AD-17, AD-18).

* ``accounts.id`` is the Supabase Auth uid. ``email`` is stored lower-cased and unique.
  ``role`` is ``student`` (default) or ``admin``.
* ``admin_audit`` is insert-only: a trigger rejects DELETE and TRUNCATE, and any UPDATE except
  setting ``actor_id`` / ``target_account_id`` to NULL from the ``ON DELETE SET NULL`` cascade
  (``pg_trigger_depth() > 1``); a direct nulling UPDATE is rejected.
* The Story 1.2 ``ops_events`` UPDATE block is relaxed the same way for ``owner_id -> NULL``.
* Foreign keys to ``accounts(id)``: ``admin_audit.actor_id`` / ``target_account_id`` and
  ``ops_events.owner_id`` SET NULL; ``schedules.owner_id`` CASCADE.

Before the foreign keys are added, rows that point at no account (none exist in a real
deployment, since accounts did not exist before this revision) are fixed: ``ops_events.owner_id``
is set to NULL and owner ``schedules`` rows are deleted, matching the declared ON DELETE actions.

Revision ID: 0004_accounts
Revises: 0003_pg_cron_drain
Create Date: 2026-11-14
"""

from collections.abc import Sequence

from alembic import op

from migrations.helpers import create_app_table_rls

revision: str = "0004_accounts"
down_revision: str | None = "0003_pg_cron_drain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPOSED = "public, anon, authenticated"

# Story 1.2 body, restored on downgrade.
OPS_EVENTS_BLOCK_ALL_UPDATES = """
CREATE OR REPLACE FUNCTION app.ops_events_block_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ops_events is append-only' USING ERRCODE = 'restrict_violation';
END;
$$
"""

# Only owner_id may change, only to NULL, and only from the ON DELETE SET NULL cascade of an
# accounts delete: the cascade's UPDATE runs inside the FK trigger, so pg_trigger_depth() > 1.
# A direct UPDATE (depth 1) is rejected.
OPS_EVENTS_ALLOW_OWNER_NULL = """
CREATE OR REPLACE FUNCTION app.ops_events_block_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF pg_trigger_depth() > 1
       AND NEW.owner_id IS NULL
       AND (to_jsonb(NEW) - 'owner_id') = (to_jsonb(OLD) - 'owner_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'ops_events is append-only' USING ERRCODE = 'restrict_violation';
END;
$$
"""

ADMIN_AUDIT_GUARD = """
CREATE FUNCTION app.admin_audit_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'admin_audit is insert-only' USING ERRCODE = 'restrict_violation';
    END IF;
    -- Only the ON DELETE SET NULL cascade (fired from the FK trigger, depth > 1) may null the
    -- account references; a direct UPDATE (depth 1) never may.
    IF pg_trigger_depth() > 1
       AND (NEW.actor_id IS NULL OR NEW.actor_id IS NOT DISTINCT FROM OLD.actor_id)
       AND (NEW.target_account_id IS NULL
            OR NEW.target_account_id IS NOT DISTINCT FROM OLD.target_account_id)
       AND (to_jsonb(NEW) - 'actor_id' - 'target_account_id')
           = (to_jsonb(OLD) - 'actor_id' - 'target_account_id') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'admin_audit is insert-only' USING ERRCODE = 'restrict_violation';
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.accounts (
            id uuid PRIMARY KEY,
            email text NOT NULL,
            role text NOT NULL DEFAULT 'student',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT accounts_email_key UNIQUE (email),
            CONSTRAINT accounts_email_lower_check CHECK (email = lower(email) AND email <> ''),
            CONSTRAINT accounts_role_check CHECK (role IN ('student', 'admin'))
        )
        """
    )
    create_app_table_rls("accounts")

    op.execute(
        """
        CREATE TABLE app.admin_audit (
            id uuid PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            actor_id uuid,
            action text NOT NULL,
            target_account_id uuid,
            CONSTRAINT admin_audit_actor_id_fkey FOREIGN KEY (actor_id)
                REFERENCES app.accounts (id) ON DELETE SET NULL,
            CONSTRAINT admin_audit_target_account_id_fkey FOREIGN KEY (target_account_id)
                REFERENCES app.accounts (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX admin_audit_created_at_idx ON app.admin_audit (created_at)")
    op.execute("CREATE INDEX admin_audit_actor_id_idx ON app.admin_audit (actor_id)")
    op.execute(
        "CREATE INDEX admin_audit_target_account_id_idx ON app.admin_audit (target_account_id)"
    )
    op.execute(ADMIN_AUDIT_GUARD)
    op.execute(
        """
        CREATE TRIGGER admin_audit_insert_only BEFORE UPDATE OR DELETE ON app.admin_audit
        FOR EACH ROW EXECUTE FUNCTION app.admin_audit_guard()
        """
    )
    op.execute(
        """
        CREATE TRIGGER admin_audit_no_truncate BEFORE TRUNCATE ON app.admin_audit
        FOR EACH STATEMENT EXECUTE FUNCTION app.admin_audit_guard()
        """
    )
    create_app_table_rls("admin_audit")

    # ops_events: allow only owner_id -> NULL, so ON DELETE SET NULL works.
    op.execute(OPS_EVENTS_ALLOW_OWNER_NULL)
    # One-off orphan fix-up: the trigger rejects direct UPDATEs, so it is disabled for this
    # statement only (inside the migration's transaction).
    op.execute("ALTER TABLE app.ops_events DISABLE TRIGGER ops_events_no_update")
    op.execute(
        """
        UPDATE app.ops_events e SET owner_id = NULL
        WHERE e.owner_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM app.accounts a WHERE a.id = e.owner_id)
        """
    )
    op.execute("ALTER TABLE app.ops_events ENABLE TRIGGER ops_events_no_update")
    op.execute(
        """
        ALTER TABLE app.ops_events ADD CONSTRAINT ops_events_owner_id_fkey
            FOREIGN KEY (owner_id) REFERENCES app.accounts (id) ON DELETE SET NULL
        """
    )
    op.execute("CREATE INDEX ops_events_owner_id_idx ON app.ops_events (owner_id)")

    op.execute(
        """
        DELETE FROM app.schedules s
        WHERE s.owner_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM app.accounts a WHERE a.id = s.owner_id)
        """
    )
    op.execute(
        """
        ALTER TABLE app.schedules ADD CONSTRAINT schedules_owner_id_fkey
            FOREIGN KEY (owner_id) REFERENCES app.accounts (id) ON DELETE CASCADE
        """
    )
    op.execute("CREATE INDEX schedules_owner_id_idx ON app.schedules (owner_id)")

    op.execute(f"REVOKE ALL ON FUNCTION app.admin_audit_guard() FROM PUBLIC, {EXPOSED}")
    op.execute(f"REVOKE ALL ON FUNCTION app.ops_events_block_update() FROM PUBLIC, {EXPOSED}")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.schedules_owner_id_idx")
    op.execute("ALTER TABLE app.schedules DROP CONSTRAINT IF EXISTS schedules_owner_id_fkey")
    op.execute("DROP INDEX IF EXISTS app.ops_events_owner_id_idx")
    op.execute("ALTER TABLE app.ops_events DROP CONSTRAINT IF EXISTS ops_events_owner_id_fkey")
    op.execute(OPS_EVENTS_BLOCK_ALL_UPDATES)
    op.execute(f"REVOKE ALL ON FUNCTION app.ops_events_block_update() FROM PUBLIC, {EXPOSED}")
    op.execute("DROP TABLE IF EXISTS app.admin_audit")
    op.execute("DROP FUNCTION IF EXISTS app.admin_audit_guard()")
    op.execute("DROP TABLE IF EXISTS app.accounts")
