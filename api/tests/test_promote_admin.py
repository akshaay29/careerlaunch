"""AD-5 / AD-18: the promote-admin CLI is the only way to grant the admin role, and it audits."""

import os
import subprocess
import sys
import uuid

import psycopg
import pytest

from app.core.config import to_libpq_url
from scripts.promote_admin import run
from tests.conftest import API_DIR, AccountFactory


def _audit_rows(url: str, target: uuid.UUID) -> list[tuple]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT actor_id, action, target_account_id, created_at FROM app.admin_audit "
            "WHERE target_account_id = %s",
            (target,),
        ).fetchall()


def _audit_count(url: str) -> int:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute("SELECT count(*) FROM app.admin_audit").fetchone()[0]  # type: ignore[index]


async def test_promote_known_email(
    accounts: AccountFactory, migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    account_id = accounts.create(role="student")
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]

    assert await run(f"  {email.upper()} ") == 0

    assert accounts.row(account_id)[2] == "admin"  # type: ignore[index]
    rows = _audit_rows(migrated_db, account_id)
    assert len(rows) == 1
    actor_id, action, target, created_at = rows[0]
    assert (actor_id, action, target) == (None, "account.promote_admin", account_id)
    assert created_at is not None
    assert "is now an admin" in capsys.readouterr().out


async def test_promote_existing_admin_is_a_no_op(
    accounts: AccountFactory, migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    account_id = accounts.create(role="admin")
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]

    assert await run(email) == 0

    assert "already an admin" in capsys.readouterr().out
    assert accounts.row(account_id)[2] == "admin"  # type: ignore[index]
    assert _audit_rows(migrated_db, account_id) == []


async def test_promote_unknown_email_changes_nothing(
    accounts: AccountFactory, migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    bystander = accounts.create(role="student")
    before = _audit_count(migrated_db)

    assert await run("nobody-here@example.test") == 1

    assert "no account with email" in capsys.readouterr().err
    assert _audit_count(migrated_db) == before
    assert accounts.row(bystander)[2] == "student"  # type: ignore[index]


def test_cli_exit_codes(accounts: AccountFactory, migrated_db: str) -> None:
    """The real entry point: exit 1 + stderr on an unknown email, 0 on a known one."""
    env = {**os.environ, "DATABASE_URL": migrated_db}
    script = str(API_DIR / "scripts" / "promote_admin.py")
    unknown = subprocess.run(
        [sys.executable, script, "ghost@example.test"],
        capture_output=True, text=True, env=env, cwd=API_DIR, timeout=60,
    )
    assert unknown.returncode == 1
    assert "no account with email" in unknown.stderr

    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    known = subprocess.run(
        [sys.executable, script, email],
        capture_output=True, text=True, env=env, cwd=API_DIR, timeout=60,
    )
    assert known.returncode == 0, known.stderr
    assert accounts.row(account_id)[2] == "admin"  # type: ignore[index]
    assert len(_audit_rows(migrated_db, account_id)) == 1
