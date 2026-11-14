"""AD-5 / AD-17 / AD-18: account provisioning, /me, insert-only admin_audit and delete actions."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from httpx import AsyncClient

from app.core.config import to_libpq_url
from app.core.db import get_sessionmaker
from app.core.ops import record_audit, record_event
from tests.conftest import AccountFactory, TokenFactory

ME = "/api/v1/me"


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(to_libpq_url(url), autocommit=True)


# --------------------------------------------------------------------------- provisioning


async def test_first_call_provisions_student(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    sub, email = accounts.new_identity()
    assert accounts.row(sub) is None

    resp = await client.get(ME, headers=tokens.headers(tokens.token(sub, email.upper())))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": str(sub), "email": email, "role": "student"}
    assert accounts.row(sub) == (sub, email, "student")


async def test_repeat_call_keeps_role_and_refreshes_email(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    account_id = accounts.create(role="admin")
    _, old_email, _ = accounts.row(account_id)  # type: ignore[misc]

    same = await client.get(ME, headers=tokens.headers(tokens.token(account_id, old_email)))
    assert same.json() == {"id": str(account_id), "email": old_email, "role": "admin"}

    new_email = f"Renamed-{account_id.hex[:8]}@Example.TEST"
    changed = await client.get(ME, headers=tokens.headers(tokens.token(account_id, new_email)))
    assert changed.status_code == 200
    assert changed.json() == {"id": str(account_id), "email": new_email.lower(), "role": "admin"}
    assert accounts.row(account_id) == (account_id, new_email.lower(), "admin")


async def test_role_claims_never_set_role_on_provisioning(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    sub, email = accounts.new_identity()
    token = tokens.token(sub, email, role="admin", app_metadata={"role": "admin"})
    resp = await client.get(ME, headers=tokens.headers(token))
    assert resp.json()["role"] == "student"


async def test_email_owned_by_another_account_is_409(
    client: AsyncClient, tokens: TokenFactory, accounts: AccountFactory
) -> None:
    existing = accounts.create()
    _, taken, _ = accounts.row(existing)  # type: ignore[misc]
    sub, _ = accounts.new_identity()
    resp = await client.get(ME, headers=tokens.headers(tokens.token(sub, taken)))
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert accounts.row(sub) is None


async def test_me_is_in_openapi_with_role(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert {"/api/v1/me", "/api/v1/admin/me", "/api/v1/public/health"} <= set(schema["paths"])
    me = schema["components"]["schemas"]["Me"]
    assert set(me["properties"]) == {"id", "email", "role"}
    assert me["properties"]["role"]["enum"] == ["student", "admin"]
    # The bearer scheme is declared, so the generated web client attaches the token; public
    # routes declare none.
    schemes = schema["components"]["securitySchemes"]
    assert {"type": "http", "scheme": "bearer"}.items() <= next(iter(schemes.values())).items()
    assert schema["paths"]["/api/v1/me"]["get"]["security"]
    assert schema["paths"]["/api/v1/admin/me"]["get"]["security"]
    assert "security" not in schema["paths"]["/api/v1/public/health"]["get"]


async def test_read_only_path_reports_no_write(accounts: AccountFactory) -> None:
    from app.modules.accounts.service import get_or_provision

    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    async with get_sessionmaker()() as session:
        actor, wrote = await get_or_provision(session, account_id, email)
        assert (actor.id, wrote) == (account_id, False)
    sub, new_email = accounts.new_identity()
    async with get_sessionmaker()() as session:
        _, wrote = await get_or_provision(session, sub, new_email)
        assert wrote is True
        await session.commit()


# --------------------------------------------------------------------------- DB constraints


def test_accounts_constraints(accounts: AccountFactory, migrated_db: str) -> None:
    with _connect(migrated_db) as conn:
        for email, role in (("Upper@Example.test", "student"), ("ok@example.test", "owner")):
            account_id = accounts.new_identity()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO app.accounts (id, email, role) VALUES (%s, %s, %s)",
                    (account_id, email, role),
                )
        account_id = accounts.new_identity()[0]
        conn.execute(
            "INSERT INTO app.accounts (id, email) VALUES (%s, %s)",
            (account_id, f"{account_id}@example.test"),
        )
        role, created_at = conn.execute(
            "SELECT role, created_at FROM app.accounts WHERE id = %s", (account_id,)
        ).fetchone()  # type: ignore[misc]
        assert role == "student" and created_at is not None


def test_new_tables_in_app_with_forced_rls(migrated_db: str) -> None:
    with _connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'app' AND c.relname IN ('accounts', 'admin_audit')"
        ).fetchall()
    assert sorted(rows) == [("accounts", True, True), ("admin_audit", True, True)]


# --------------------------------------------------------------------------- admin_audit


async def _audit(actor: uuid.UUID | None, target: uuid.UUID | None) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        audit_id = await record_audit(
            session, "account.test_action", actor_id=actor, target_account_id=target
        )
        await session.commit()
    return audit_id


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE app.admin_audit SET action = 'account.tampered' WHERE id = %s",
        "UPDATE app.admin_audit SET created_at = now() - interval '1 day' WHERE id = %s",
        "UPDATE app.admin_audit SET target_account_id = gen_random_uuid() WHERE id = %s",
        "DELETE FROM app.admin_audit WHERE id = %s",
        "UPDATE app.admin_audit SET actor_id = NULL WHERE id = %s",
        "UPDATE app.admin_audit SET target_account_id = NULL WHERE id = %s",
    ],
    ids=["update-action", "update-created-at", "retarget", "delete", "null-actor", "null-target"],
)
async def test_admin_audit_tamper_is_rejected(
    accounts: AccountFactory, migrated_db: str, statement: str
) -> None:
    actor, target = accounts.create(role="admin"), accounts.create()
    audit_id = await _audit(actor, target)
    with _connect(migrated_db) as conn:
        with pytest.raises(psycopg.errors.RestrictViolation, match="insert-only"):
            conn.execute(statement, (audit_id,))


def test_admin_audit_truncate_is_rejected(migrated_db: str) -> None:
    with _connect(migrated_db) as conn:
        with pytest.raises(psycopg.errors.RestrictViolation, match="insert-only"):
            conn.execute("TRUNCATE app.admin_audit")


async def test_record_audit_rejects_free_text() -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError):
            await record_audit(session, "Alice promoted Bob", actor_id=None, target_account_id=None)
        with pytest.raises(ValueError):
            await record_audit(session, "account.x", actor_id="a@b.c", target_account_id=None)  # type: ignore[arg-type]


async def test_ops_events_owner_can_only_be_nulled(accounts: AccountFactory, migrated_db: str) -> None:
    owner, other = accounts.create(), accounts.create()
    async with get_sessionmaker()() as session:
        event_id = await record_event(session, "x.test", owner_id=owner)
        await session.commit()
    with _connect(migrated_db) as conn:
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute("UPDATE app.ops_events SET owner_id = %s WHERE id = %s", (other, event_id))
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(
                "UPDATE app.ops_events SET owner_id = NULL, kind = 'x.changed' WHERE id = %s",
                (event_id,),
            )
        # Nulling is reserved for the ON DELETE SET NULL cascade; a direct UPDATE is rejected.
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute("UPDATE app.ops_events SET owner_id = NULL WHERE id = %s", (event_id,))
        conn.execute("DELETE FROM app.ops_events WHERE id = %s", (event_id,))


async def test_ops_events_owner_must_be_an_account() -> None:
    from sqlalchemy.exc import IntegrityError

    async with get_sessionmaker()() as session:
        with pytest.raises(IntegrityError):
            await record_event(session, "x.test", owner_id=uuid.uuid4())
            await session.commit()
        await session.rollback()


# --------------------------------------------------------------------------- account deletion


async def test_deleting_account_nulls_audit_and_ops_and_cascades_schedules(
    accounts: AccountFactory, migrated_db: str
) -> None:
    doomed, admin = accounts.create(), accounts.create(role="admin")
    by_doomed = await _audit(doomed, admin)  # doomed as actor
    on_doomed = await _audit(admin, doomed)  # doomed as target
    self_audit = await _audit(doomed, doomed)  # both columns
    async with get_sessionmaker()() as session:
        event_id = await record_event(session, "x.test", owner_id=doomed, counts={"n": 1})
        await session.commit()
    schedule_id, other_schedule = uuid.uuid7(), uuid.uuid7()
    with _connect(migrated_db) as conn:
        for sid, owner in ((schedule_id, doomed), (other_schedule, admin)):
            conn.execute(
                'INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval") '
                "VALUES (%s, 'tests.deletion', %s, %s, interval '1 day')",
                (sid, owner, datetime.now(UTC) + timedelta(days=1)),
            )

        conn.execute("DELETE FROM app.accounts WHERE id = %s", (doomed,))

        audits = dict(
            (r[0], r[1:])
            for r in conn.execute(
                "SELECT id, actor_id, target_account_id, action FROM app.admin_audit "
                "WHERE id = ANY(%s)",
                ([by_doomed, on_doomed, self_audit],),
            )
        )
        assert audits[by_doomed] == (None, admin, "account.test_action")
        assert audits[on_doomed] == (admin, None, "account.test_action")
        assert audits[self_audit] == (None, None, "account.test_action")
        owner_id, kind, counts = conn.execute(
            "SELECT owner_id, kind, counts FROM app.ops_events WHERE id = %s", (event_id,)
        ).fetchone()  # type: ignore[misc]
        assert (owner_id, kind, counts) == (None, "x.test", {"n": 1})
        remaining = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM app.schedules WHERE id = ANY(%s)", ([schedule_id, other_schedule],)
            )
        }
        assert remaining == {other_schedule}
        conn.execute("DELETE FROM app.schedules WHERE id = %s", (other_schedule,))
        conn.execute("DELETE FROM app.ops_events WHERE id = %s", (event_id,))
