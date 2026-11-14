"""AD-11 / FR-2: versioned consent records, the require_consent gate and ConsentRevoked."""

import asyncio
import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from app.core import events
from app.core.config import to_libpq_url
from app.core.db import get_sessionmaker
from app.core.errors import ConsentRequiredError
from app.core.events import ConsentRevoked, subscribe
from app.modules.accounts import consent_text
from app.modules.accounts.consent_text import ConsentPurpose
from app.modules.accounts.service import require_consent
from tests.conftest import AccountFactory, TokenFactory

CONSENTS = "/api/v1/consents"
PROVIDER = ConsentPurpose.PROVIDER_RUNS
POOL = ConsentPurpose.POOL_CONTRIBUTION


def _current(purpose: ConsentPurpose) -> str:
    return consent_text.current_text(purpose).version


def _rows(url: str, owner_id: uuid.UUID, purpose: str | None = None) -> list[tuple]:
    """(purpose, version, granted_at, revoked_at) for the owner, oldest first."""
    sql = "SELECT purpose, version, granted_at, revoked_at FROM app.consents WHERE owner_id = %s"
    params: list[Any] = [owner_id]
    if purpose is not None:
        sql += " AND purpose = %s"
        params.append(purpose)
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(sql + " ORDER BY granted_at, id", params).fetchall()


def _end_reasons(url: str, owner_id: uuid.UUID) -> list[tuple[str, str | None]]:
    """(version, end_reason) for the owner, oldest first."""
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT version, end_reason FROM app.consents WHERE owner_id = %s "
            "ORDER BY granted_at, id",
            (owner_id,),
        ).fetchall()


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> tuple[uuid.UUID, dict[str, str]]:
    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, tokens.headers(tokens.token(account_id, email))


@pytest.fixture
def revoked_events() -> Iterator[list[tuple[ConsentRevoked, Any]]]:
    """Subscribe a recorder to ConsentRevoked; the registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[tuple[ConsentRevoked, Any]] = []

    async def record(session: Any, event: ConsentRevoked) -> None:
        seen.append((event, session))

    subscribe(ConsentRevoked, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


async def _grant(client: AsyncClient, headers: dict[str, str], purpose: str, version: str | None = None):
    return await client.put(
        f"{CONSENTS}/{purpose}", json={"version": version or _current(ConsentPurpose(purpose))}, headers=headers
    )


# --------------------------------------------------------------------------- GET


async def test_first_view_lists_both_purposes_with_text_and_rights(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]]
) -> None:
    _, headers = student
    resp = await client.get(CONSENTS, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [p["purpose"] for p in body["purposes"]] == ["provider_runs", "pool_contribution"]
    for p in body["purposes"]:
        assert p["status"] == "none"
        assert p["granted_at"] is None and p["revoked_at"] is None
        assert p["version"] == _current(ConsentPurpose(p["purpose"]))
        assert p["title"] and p["body"] and all(isinstance(x, str) for x in p["body"])
    provider_text = " ".join(body["purposes"][0]["body"])
    assert "your own Apify account" in provider_text and "LinkedIn" in provider_text
    pool_text = " ".join(body["purposes"][1]["body"])
    assert "Other CareerLaunch Students" in pool_text and "visible only to you" in pool_text
    rights = " ".join(body["rights"]["body"])
    assert "Digital Personal Data Protection Act" in body["rights"]["title"]
    for word in ("Access", "Correction", "Erasure", "Withdrawing consent", "Grievance", "30 days"):
        assert word in rights
    assert body["notice_version"] == consent_text.RIGHTS_NOTICE_VERSION


# --------------------------------------------------------------------------- PUT


async def test_grant_creates_one_active_row_and_is_idempotent(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], migrated_db: str
) -> None:
    owner, headers = student
    resp = await _grant(client, headers, "provider_runs")
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["purpose"] == "provider_runs" and first["status"] == "granted"
    assert first["granted_at"] is not None and first["revoked_at"] is None
    rows = _rows(migrated_db, owner)
    assert len(rows) == 1
    assert rows[0][:2] == ("provider_runs", _current(PROVIDER)) and rows[0][3] is None

    again = await _grant(client, headers, "provider_runs")
    assert again.status_code == 200
    assert again.json()["granted_at"] == first["granted_at"]
    assert len(_rows(migrated_db, owner)) == 1

    listed = {p["purpose"]: p["status"] for p in (await client.get(CONSENTS, headers=headers)).json()["purposes"]}
    assert listed == {"provider_runs": "granted", "pool_contribution": "none"}


async def test_stale_version_is_409_and_writes_nothing(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], migrated_db: str
) -> None:
    owner, headers = student
    resp = await _grant(client, headers, "pool_contribution", version="2000-01-01")
    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")
    problem = resp.json()
    assert problem["type"] == "/problems/consent-version-mismatch"
    assert problem["purpose"] == "pool_contribution"
    assert problem["current_version"] == _current(POOL)
    assert _rows(migrated_db, owner) == []


@pytest.mark.parametrize("method", ["put", "delete"])
async def test_unknown_purpose_is_422(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], method: str
) -> None:
    _, headers = student
    kwargs: dict[str, Any] = {"headers": headers}
    if method == "put":
        kwargs["json"] = {"version": "2026-11-14"}
    resp = await client.request(method.upper(), f"{CONSENTS}/outreach", **kwargs)
    assert resp.status_code == 422
    assert resp.json()["type"] == "/problems/validation-error"


async def test_missing_version_is_422(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]]
) -> None:
    _, headers = student
    resp = await client.put(f"{CONSENTS}/provider_runs", json={}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", CONSENTS), ("PUT", f"{CONSENTS}/provider_runs"), ("DELETE", f"{CONSENTS}/provider_runs")],
)
async def test_no_token_is_401(client: AsyncClient, method: str, path: str) -> None:
    kwargs: dict[str, Any] = {"json": {"version": "2026-11-14"}} if method == "PUT" else {}
    resp = await client.request(method, path, **kwargs)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


# --------------------------------------------------------------------------- DELETE


async def test_revoke_sets_revoked_at_and_publishes_once_before_commit(
    client: AsyncClient,
    student: tuple[uuid.UUID, dict[str, str]],
    migrated_db: str,
) -> None:
    owner, headers = student
    await _grant(client, headers, "provider_runs")
    observed: list[dict[str, Any]] = []
    saved = {k: list(v) for k, v in events._subscribers.items()}

    async def check(session: Any, event: ConsentRevoked) -> None:
        # Same session: the row is already revoked.
        in_session = (
            await session.execute(
                text(
                    "SELECT revoked_at FROM consents WHERE owner_id = :o AND purpose = :p"
                ),
                {"o": event.owner_id, "p": event.purpose},
            )
        ).scalar_one()
        # Another connection: nothing committed yet.
        committed = _rows(migrated_db, event.owner_id, event.purpose)
        observed.append({"event": event, "in_session": in_session, "committed": committed})

    subscribe(ConsentRevoked, check)
    try:
        resp = await client.delete(f"{CONSENTS}/provider_runs", headers=headers)
    finally:
        events.clear_subscribers()
        events._subscribers.update(saved)

    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert len(observed) == 1
    seen = observed[0]
    assert seen["event"] == ConsentRevoked(owner_id=owner, purpose="provider_runs")
    assert seen["in_session"] is not None
    assert len(seen["committed"]) == 1 and seen["committed"][0][3] is None

    rows = _rows(migrated_db, owner)
    assert len(rows) == 1 and rows[0][3] is not None
    listed = (await client.get(CONSENTS, headers=headers)).json()["purposes"][0]
    assert listed["status"] == "revoked" and listed["revoked_at"] is not None


async def test_revoke_with_nothing_active_writes_and_publishes_nothing(
    client: AsyncClient,
    student: tuple[uuid.UUID, dict[str, str]],
    migrated_db: str,
    revoked_events: list,
) -> None:
    owner, headers = student
    resp = await client.delete(f"{CONSENTS}/pool_contribution", headers=headers)
    assert resp.status_code == 204
    assert _rows(migrated_db, owner) == []
    assert revoked_events == []

    # Already revoked: still nothing new.
    await _grant(client, headers, "pool_contribution")
    await client.delete(f"{CONSENTS}/pool_contribution", headers=headers)
    before = _rows(migrated_db, owner)
    assert len(revoked_events) == 1
    resp = await client.delete(f"{CONSENTS}/pool_contribution", headers=headers)
    assert resp.status_code == 204
    assert _rows(migrated_db, owner) == before
    assert len(revoked_events) == 1


async def test_failing_subscriber_rolls_back_revocation(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], migrated_db: str
) -> None:
    owner, headers = student
    await _grant(client, headers, "provider_runs")
    saved = {k: list(v) for k, v in events._subscribers.items()}

    async def boom(_session: Any, _event: ConsentRevoked) -> None:
        raise RuntimeError("subscriber failed")

    subscribe(ConsentRevoked, boom)
    try:
        resp = await client.delete(f"{CONSENTS}/provider_runs", headers=headers)
    finally:
        events.clear_subscribers()
        events._subscribers.update(saved)

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert "subscriber failed" not in resp.text
    rows = _rows(migrated_db, owner)
    assert len(rows) == 1 and rows[0][3] is None


async def test_regrant_after_revoke_keeps_history(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], migrated_db: str
) -> None:
    owner, headers = student
    await _grant(client, headers, "provider_runs")
    await client.delete(f"{CONSENTS}/provider_runs", headers=headers)
    resp = await _grant(client, headers, "provider_runs")
    assert resp.status_code == 200 and resp.json()["status"] == "granted"
    rows = _rows(migrated_db, owner)
    assert len(rows) == 2
    assert rows[0][3] is not None and rows[1][3] is None
    # GET picks the active row over the older revoked one.
    listed = (await client.get(CONSENTS, headers=headers)).json()["purposes"][0]
    assert listed["status"] == "granted" and listed["revoked_at"] is None


async def test_concurrent_grants_are_idempotent(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]], migrated_db: str
) -> None:
    owner, headers = student
    results = await asyncio.gather(
        _grant(client, headers, "provider_runs"), _grant(client, headers, "provider_runs")
    )
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    rows = _rows(migrated_db, owner)
    assert len(rows) == 1 and rows[0][3] is None


async def test_end_reason_distinguishes_supersede_from_withdrawal(
    client: AsyncClient,
    student: tuple[uuid.UUID, dict[str, str]],
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, headers = student
    old = _current(PROVIDER)
    await _grant(client, headers, "provider_runs")
    bumped = dataclasses.replace(consent_text.CONSENT_TEXTS[PROVIDER], version="2099-01-01")
    monkeypatch.setitem(consent_text.CONSENT_TEXTS, PROVIDER, bumped)
    await _grant(client, headers, "provider_runs", version="2099-01-01")
    await client.delete(f"{CONSENTS}/provider_runs", headers=headers)
    assert _end_reasons(migrated_db, owner) == [(old, "superseded"), ("2099-01-01", "revoked")]


async def test_withdrawing_outdated_consent_publishes_once(
    client: AsyncClient,
    student: tuple[uuid.UUID, dict[str, str]],
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
    revoked_events: list,
) -> None:
    owner, headers = student
    await _grant(client, headers, "pool_contribution")
    bumped = dataclasses.replace(consent_text.CONSENT_TEXTS[POOL], version="2099-01-01")
    monkeypatch.setitem(consent_text.CONSENT_TEXTS, POOL, bumped)
    listed = (await client.get(CONSENTS, headers=headers)).json()["purposes"][1]
    assert listed["status"] == "outdated"

    resp = await client.delete(f"{CONSENTS}/pool_contribution", headers=headers)
    assert resp.status_code == 204
    assert [e for e, _ in revoked_events] == [
        ConsentRevoked(owner_id=owner, purpose="pool_contribution")
    ]
    assert [reason for _, reason in _end_reasons(migrated_db, owner)] == ["revoked"]


# --------------------------------------------------------------------------- versions and gate


async def test_new_version_makes_consent_outdated_until_granted_again(
    client: AsyncClient,
    student: tuple[uuid.UUID, dict[str, str]],
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
    revoked_events: list,
) -> None:
    owner, headers = student
    old = _current(PROVIDER)
    await _grant(client, headers, "provider_runs")
    async with get_sessionmaker()() as session:
        await require_consent(session, owner, PROVIDER)

    bumped = dataclasses.replace(consent_text.CONSENT_TEXTS[PROVIDER], version="2099-01-01")
    monkeypatch.setitem(consent_text.CONSENT_TEXTS, PROVIDER, bumped)

    listed = (await client.get(CONSENTS, headers=headers)).json()["purposes"][0]
    assert (listed["status"], listed["version"]) == ("outdated", "2099-01-01")
    async with get_sessionmaker()() as session:
        with pytest.raises(ConsentRequiredError):
            await require_consent(session, owner, PROVIDER)

    assert (await _grant(client, headers, "provider_runs", version=old)).status_code == 409
    resp = await _grant(client, headers, "provider_runs", version="2099-01-01")
    assert resp.status_code == 200 and resp.json()["status"] == "granted"
    rows = _rows(migrated_db, owner)
    assert [(r[1], r[3] is None) for r in rows] == [(old, False), ("2099-01-01", True)]
    assert revoked_events == []  # superseding is not a withdrawal
    async with get_sessionmaker()() as session:
        await require_consent(session, owner, PROVIDER)


async def test_require_consent_cases(
    client: AsyncClient, student: tuple[uuid.UUID, dict[str, str]]
) -> None:
    owner, headers = student

    async def gate(purpose: ConsentPurpose | str) -> None:
        async with get_sessionmaker()() as session:
            await require_consent(session, owner, purpose)

    with pytest.raises(ConsentRequiredError) as none:
        await gate(PROVIDER)
    assert none.value.purpose == "provider_runs"

    await _grant(client, headers, "provider_runs")
    await gate(PROVIDER)
    await gate("provider_runs")
    with pytest.raises(ConsentRequiredError):  # purposes are independent
        await gate(POOL)

    await client.delete(f"{CONSENTS}/provider_runs", headers=headers)
    with pytest.raises(ConsentRequiredError):
        await gate(PROVIDER)

    with pytest.raises(ValueError):
        await gate("outreach")


async def test_consent_required_maps_to_403_problem(app: FastAPI, client: AsyncClient) -> None:
    async def gated() -> None:
        raise ConsentRequiredError("pool_contribution")

    app.add_api_route("/test-consent-gate", gated)
    resp = await client.get("/test-consent-gate")
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "/problems/consent-required"
    assert body["purpose"] == "pool_contribution"
    assert body["status"] == 403 and body["instance"] == "/test-consent-gate"


# --------------------------------------------------------------------------- isolation and DB


async def test_students_see_and_change_only_their_own_rows(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    migrated_db: str,
) -> None:
    def login() -> tuple[uuid.UUID, dict[str, str]]:
        account_id = accounts.create()
        _, email, _ = accounts.row(account_id)  # type: ignore[misc]
        return account_id, tokens.headers(tokens.token(account_id, email))

    a, a_headers = login()
    b, b_headers = login()
    await _grant(client, a_headers, "provider_runs")
    await _grant(client, a_headers, "pool_contribution")

    b_view = (await client.get(CONSENTS, headers=b_headers)).json()["purposes"]
    assert {p["status"] for p in b_view} == {"none"}
    assert (await client.delete(f"{CONSENTS}/provider_runs", headers=b_headers)).status_code == 204
    await _grant(client, b_headers, "pool_contribution")

    a_rows = _rows(migrated_db, a)
    assert len(a_rows) == 2 and all(r[3] is None for r in a_rows)
    assert [r[0] for r in _rows(migrated_db, b)] == ["pool_contribution"]


def test_consents_table_constraints(accounts: AccountFactory, migrated_db: str) -> None:
    owner = accounts.create()
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        insert = "INSERT INTO app.consents (id, owner_id, purpose, version) VALUES (%s, %s, %s, %s)"
        conn.execute(insert, (uuid.uuid7(), owner, "provider_runs", "v1"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (uuid.uuid7(), owner, "provider_runs", "v1"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (uuid.uuid7(), owner, "outreach", "v1"))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(insert, (uuid.uuid7(), uuid.uuid4(), "provider_runs", "v1"))
        with pytest.raises(psycopg.errors.CheckViolation):  # revoked_at without end_reason
            conn.execute(
                "UPDATE app.consents SET revoked_at = now() WHERE owner_id = %s", (owner,)
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # unknown end_reason
            conn.execute(
                "UPDATE app.consents SET revoked_at = now(), end_reason = 'expired' "
                "WHERE owner_id = %s",
                (owner,),
            )
        conn.execute(
            "UPDATE app.consents SET revoked_at = now(), end_reason = 'revoked' "
            "WHERE owner_id = %s",
            (owner,),
        )
        conn.execute(insert, (uuid.uuid7(), owner, "provider_runs", "v1"))  # history allowed
        (rls, forced) = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'app.consents'::regclass"
        ).fetchone()  # type: ignore[misc]
        assert rls and forced


def test_deleting_account_cascades_consents(accounts: AccountFactory, migrated_db: str) -> None:
    owner = accounts.create()
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.consents (id, owner_id, purpose, version) VALUES (%s, %s, %s, %s)",
            (uuid.uuid7(), owner, "pool_contribution", "v1"),
        )
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert _rows(migrated_db, owner) == []


async def test_consent_routes_in_openapi(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert set(paths[CONSENTS]) == {"get"}
    assert set(paths[f"{CONSENTS}/{{purpose}}"]) == {"put", "delete"}
    for op in (paths[CONSENTS]["get"], paths[f"{CONSENTS}/{{purpose}}"]["put"]):
        assert op["security"]
    assert schema["components"]["schemas"]["ConsentPurpose"]["enum"] == [
        "provider_runs",
        "pool_contribution",
    ]
