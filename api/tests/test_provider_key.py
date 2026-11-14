"""FR-28 / AD-6: ``GET``/``PUT``/``DELETE /api/v1/provider-key``.

Apify is the in-memory FakeProviderRunner; the master key is a random test key.
"""

import base64
import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core import events
from app.core.config import get_settings, to_libpq_url
from app.core.db import get_sessionmaker
from app.core.events import ProviderKeyChanged, subscribe
from app.main import create_app
from app.modules.accounts import COLLECTION_SCHEDULE_KINDS, service
from app.modules.accounts.consent_text import ConsentPurpose, current_text
from app.ports import provider_runner as provider_runner_port
from app.ports.provider_runner import ProviderAccount
from tests.conftest import AccountFactory, TokenFactory
from app.modules.accounts.schemas import TOKEN_FORMAT_MESSAGE
from tests.fakes.provider_runner import FakeProviderRunner

KEY_URL = "/api/v1/provider-key"
PROBLEM = "application/problem+json"
TOKEN = "apify_api_FirstToken0123456789abcdefWXYZ"  # ggignore -- fake test token
TOKEN_2 = "apify_api_SecondToken9876543210zyxwvuQRST"  # ggignore -- fake test token
INVALID_MSG = (
    "Apify didn't accept this token. Check that you copied it fully and that it hasn't expired."
)


@pytest.fixture
def runner() -> FakeProviderRunner:
    return FakeProviderRunner()


@pytest.fixture
def app(runner: FakeProviderRunner):
    saved = provider_runner_port._installed
    yield create_app(provider_runner=runner)
    provider_runner_port._installed = saved


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch, tokens: TokenFactory) -> Iterator[str]:
    """A random CREDENTIALS_MASTER_KEYS (depends on ``tokens`` so its env is set first)."""
    value = "1:" + base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("CREDENTIALS_MASTER_KEYS", value)
    get_settings.cache_clear()
    yield value
    monkeypatch.delenv("CREDENTIALS_MASTER_KEYS", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def key_events() -> Iterator[list[ProviderKeyChanged]]:
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[ProviderKeyChanged] = []

    async def record(session: Any, event: ProviderKeyChanged) -> None:
        seen.append(event)

    subscribe(ProviderKeyChanged, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(to_libpq_url(url), autocommit=True)


def _grant_consent(url: str, owner_id: uuid.UUID) -> None:
    version = current_text(ConsentPurpose.PROVIDER_RUNS).version
    with _connect(url) as conn:
        conn.execute(
            "INSERT INTO app.consents (id, owner_id, purpose, version) VALUES (%s, %s, %s, %s)",
            (uuid.uuid7(), owner_id, "provider_runs", version),
        )


def _rows(url: str, owner_id: uuid.UUID) -> list[dict]:
    with _connect(url) as conn:
        cur = conn.execute(
            "SELECT id, owner_id, provider, ciphertext, key_version, last4, provider_user_id, "
            "validated_at, created_at, updated_at FROM app.provider_credentials "
            "WHERE owner_id = %s",
            (owner_id,),
        )
        cols = [c.name for c in cur.description or ()]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


class Student:
    def __init__(self, accounts: AccountFactory, tokens: TokenFactory, consent: bool) -> None:
        self.id = accounts.create()
        _, email, _ = accounts.row(self.id)  # type: ignore[misc]
        self.headers = tokens.headers(tokens.token(self.id, email))
        if consent:
            _grant_consent(accounts.url, self.id)


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory, master_key: str) -> Student:
    return Student(accounts, tokens, consent=True)


@pytest.fixture
def make_student(accounts: AccountFactory, tokens: TokenFactory, master_key: str):
    return lambda consent=True: Student(accounts, tokens, consent)


async def _put(client: AsyncClient, s: Student, token: str = TOKEN):
    return await client.put(KEY_URL, json={"token": token}, headers=s.headers)


# --------------------------------------------------------------------------- GET


async def test_get_without_a_key(client: AsyncClient, student: Student) -> None:
    resp = await client.get(KEY_URL, headers=student.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "provider": "apify",
        "has_key": False,
        "last4": None,
        "validated_at": None,
        "needs_reentry": False,
    }


async def test_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get(KEY_URL)).status_code == 401
    assert (await client.put(KEY_URL, json={"token": TOKEN})).status_code == 401
    assert (await client.delete(KEY_URL)).status_code == 401


# --------------------------------------------------------------------------- PUT


async def test_save_valid(
    client: AsyncClient,
    student: Student,
    runner: FakeProviderRunner,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    resp = await _put(client, student, f"  {TOKEN}\n")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "apify" and body["has_key"] is True
    assert body["last4"] == TOKEN[-4:] and body["validated_at"]
    assert body["needs_reentry"] is False
    assert TOKEN not in resp.text
    assert runner.validated == [TOKEN]  # trimmed before validation
    (row,) = _rows(migrated_db, student.id)
    assert row["provider"] == "apify" and row["key_version"] == 1
    assert row["last4"] == TOKEN[-4:] and row["provider_user_id"] == "apify-user-1"
    assert bytes(row["ciphertext"]) != TOKEN.encode()
    assert key_events == [ProviderKeyChanged(owner_id=student.id, removed=False)]

    got = await client.get(KEY_URL, headers=student.headers)
    assert got.json() == body


async def test_raw_row_holds_no_token_fragment(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    assert (await _put(client, student)).status_code == 200
    (row,) = _rows(migrated_db, student.id)
    fragments = [TOKEN, TOKEN[:8], TOKEN[-8:]]
    for column, value in row.items():
        if column == "last4":
            continue
        raw = bytes(value) if isinstance(value, (bytes, memoryview)) else str(value).encode()
        for fragment in fragments:
            assert fragment.encode() not in raw, column


async def test_saved_key_decrypts_for_adapter_callers(
    client: AsyncClient, student: Student
) -> None:
    assert (await _put(client, student)).status_code == 200
    async with get_sessionmaker()() as session:
        secret = await service.decrypt_credential(session, student.id)
    assert isinstance(secret, SecretStr) and secret.get_secret_value() == TOKEN


async def test_no_consent(
    client: AsyncClient,
    make_student,
    runner: FakeProviderRunner,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    s = make_student(consent=False)
    resp = await _put(client, s)
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == "/problems/consent-required" and body["purpose"] == "provider_runs"
    assert runner.validated == []
    assert _rows(migrated_db, s.id) == [] and key_events == []


async def test_invalid_token(
    client: AsyncClient,
    student: Student,
    runner: FakeProviderRunner,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    runner.mode = "invalid"
    resp = await _put(client, student)
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "/problems/validation-error"
    assert body["errors"] == [{"loc": ["body", "token"], "msg": INVALID_MSG}]
    assert TOKEN not in resp.text
    assert _rows(migrated_db, student.id) == [] and key_events == []


async def test_apify_down(
    client: AsyncClient,
    student: Student,
    runner: FakeProviderRunner,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    runner.mode = "unavailable"
    resp = await _put(client, student)
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/provider-unavailable"
    assert _rows(migrated_db, student.id) == [] and key_events == []


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "x" * 201,
        "abcd",  # 4 characters: last4 would be the whole token
        "abcdefg",  # 7 characters
        "apify_api_\u201cquoted\u201d",  # smart quotes
        "apify_api_abc\u00a0def",  # NBSP
        "apify_api_abc\ndef",  # internal newline
        "apify_api_abc def",  # internal space
        "apify_api_abc\x01def",  # control character
        "apify_api_\u00e9t\u00e9123",  # non-ASCII letters
    ],
)
async def test_token_format_is_validated(
    client: AsyncClient, student: Student, runner: FakeProviderRunner, token: str, migrated_db: str
) -> None:
    resp = await _put(client, student, token)
    assert resp.status_code == 422, resp.text
    (error,) = resp.json()["errors"]
    assert error["loc"] == ["body", "token"]
    assert error["msg"] == TOKEN_FORMAT_MESSAGE
    assert runner.validated == []
    assert _rows(migrated_db, student.id) == []


async def test_eight_printable_characters_are_accepted(
    client: AsyncClient, student: Student, runner: FakeProviderRunner
) -> None:
    resp = await _put(client, student, "  a!b~c#d9  ")
    assert resp.status_code == 200, resp.text
    assert runner.validated == ["a!b~c#d9"] and resp.json()["last4"] == "c#d9"


async def test_no_master_key(
    client: AsyncClient,
    student: Student,
    runner: FakeProviderRunner,
    monkeypatch: pytest.MonkeyPatch,
    migrated_db: str,
) -> None:
    monkeypatch.delenv("CREDENTIALS_MASTER_KEYS", raising=False)
    get_settings.cache_clear()
    resp = await _put(client, student)
    assert resp.status_code == 503
    assert resp.json()["type"] == "/problems/credentials-unavailable"
    assert runner.validated == []
    assert _rows(migrated_db, student.id) == []
    # The app still serves everything else.
    assert (await client.get(KEY_URL, headers=student.headers)).status_code == 200
    assert (await client.get("/api/v1/public/health")).status_code == 200


async def test_no_transaction_is_open_while_apify_is_called(
    app, client: AsyncClient, student: Student, migrated_db: str
) -> None:
    seen: list[int] = []

    class Probe(FakeProviderRunner):
        async def validate_token(self, token: SecretStr) -> ProviderAccount:
            with _connect(migrated_db) as conn:
                (count,) = conn.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state LIKE 'idle in transaction%%' AND pid <> pg_backend_pid()"
                ).fetchone()  # type: ignore[misc]
            seen.append(count)
            return await super().validate_token(token)

    app.state.provider_runner = Probe()
    assert (await _put(client, student)).status_code == 200
    assert seen == [0]


async def test_replace_keeps_the_row_id(
    client: AsyncClient,
    student: Student,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    assert (await _put(client, student, TOKEN)).status_code == 200
    (first,) = _rows(migrated_db, student.id)
    resp = await _put(client, student, TOKEN_2)
    assert resp.status_code == 200 and resp.json()["last4"] == TOKEN_2[-4:]
    (second,) = _rows(migrated_db, student.id)
    assert second["id"] == first["id"]
    assert bytes(second["ciphertext"]) != bytes(first["ciphertext"])
    assert second["last4"] == TOKEN_2[-4:]
    assert second["updated_at"] >= first["updated_at"]
    async with get_sessionmaker()() as session:
        secret = await service.decrypt_credential(session, student.id)
    assert secret is not None and secret.get_secret_value() == TOKEN_2
    assert len(key_events) == 2 and not any(e.removed for e in key_events)


# --------------------------------------------------------------------------- DELETE


def _add_schedule(url: str, owner_id: uuid.UUID, kind: str) -> uuid.UUID:
    """The owner's unpaused schedule of ``kind`` (saving a key already creates the
    ``listings.collect`` one, Story 2.3)."""
    with _connect(url) as conn:
        row = conn.execute(
            'INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval") '
            "VALUES (%s, %s, %s, now() + interval '1 hour', interval '1 day') "
            "ON CONFLICT ON CONSTRAINT schedules_kind_owner_key DO UPDATE SET paused = false "
            "RETURNING id",
            (uuid.uuid7(), kind, owner_id),
        ).fetchone()
    assert row is not None
    return row[0]


def _paused(url: str, schedule_id: uuid.UUID) -> bool:
    with _connect(url) as conn:
        row = conn.execute("SELECT paused FROM app.schedules WHERE id = %s", (schedule_id,)).fetchone()
    assert row is not None
    return row[0]


@pytest.fixture
def schedules(migrated_db: str) -> Iterator[list[uuid.UUID]]:
    ids: list[uuid.UUID] = []
    yield ids
    with _connect(migrated_db) as conn:
        conn.execute("DELETE FROM app.schedules WHERE id = ANY(%s)", (ids,))


async def test_delete(
    client: AsyncClient,
    student: Student,
    key_events: list[ProviderKeyChanged],
    schedules: list[uuid.UUID],
    migrated_db: str,
) -> None:
    assert COLLECTION_SCHEDULE_KINDS == ("listings.collect",)
    assert (await _put(client, student)).status_code == 200
    collect = _add_schedule(migrated_db, student.id, "listings.collect")
    other = _add_schedule(migrated_db, student.id, "matching.other_kind")
    schedules += [collect, other]
    key_events.clear()

    resp = await client.delete(KEY_URL, headers=student.headers)
    assert resp.status_code == 204
    assert _rows(migrated_db, student.id) == []
    assert _paused(migrated_db, collect) is True
    assert _paused(migrated_db, other) is False
    assert key_events == [ProviderKeyChanged(owner_id=student.id, removed=True)]
    got = (await client.get(KEY_URL, headers=student.headers)).json()
    assert got["has_key"] is False and got["last4"] is None


async def test_delete_none(
    client: AsyncClient,
    student: Student,
    key_events: list[ProviderKeyChanged],
    schedules: list[uuid.UUID],
    migrated_db: str,
) -> None:
    collect = _add_schedule(migrated_db, student.id, "listings.collect")
    schedules.append(collect)
    resp = await client.delete(KEY_URL, headers=student.headers)
    assert resp.status_code == 204
    # No key, but a leftover collection schedule is still paused; no event.
    assert _paused(migrated_db, collect) is True
    assert key_events == []


async def test_readd_after_delete_gets_a_new_id(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    assert (await _put(client, student)).status_code == 200
    (first,) = _rows(migrated_db, student.id)
    assert (await client.delete(KEY_URL, headers=student.headers)).status_code == 204
    assert (await _put(client, student)).status_code == 200
    (second,) = _rows(migrated_db, student.id)
    assert second["id"] != first["id"]


# --------------------------------------------------------------------------- isolation


async def test_isolation(client: AsyncClient, make_student, migrated_db: str) -> None:
    a, b = make_student(), make_student()
    assert (await _put(client, a, TOKEN)).status_code == 200
    got_b = (await client.get(KEY_URL, headers=b.headers)).json()
    assert got_b["has_key"] is False
    assert (await _put(client, b, TOKEN_2)).status_code == 200
    assert (await client.get(KEY_URL, headers=a.headers)).json()["last4"] == TOKEN[-4:]
    assert (await client.get(KEY_URL, headers=b.headers)).json()["last4"] == TOKEN_2[-4:]
    # b deleting removes only b's key.
    assert (await client.delete(KEY_URL, headers=b.headers)).status_code == 204
    assert len(_rows(migrated_db, a.id)) == 1


async def test_ciphertext_moved_to_another_row_does_not_decrypt(
    client: AsyncClient, make_student, migrated_db: str
) -> None:
    from app.core.crypto import CredentialError
    from app.core.errors import ProviderKeyUnreadableError

    a, b = make_student(), make_student()
    assert (await _put(client, a, TOKEN)).status_code == 200
    assert (await _put(client, b, TOKEN_2)).status_code == 200
    with _connect(migrated_db) as conn:
        conn.execute(
            "UPDATE app.provider_credentials SET ciphertext = "
            "(SELECT ciphertext FROM app.provider_credentials WHERE owner_id = %s) "
            "WHERE owner_id = %s",
            (a.id, b.id),
        )
    async with get_sessionmaker()() as session:
        with pytest.raises(ProviderKeyUnreadableError) as exc:
            await service.decrypt_credential(session, b.id)
    assert exc.value.status == 409 and exc.value.slug == "provider-key-unreadable"
    assert not isinstance(exc.value, CredentialError)


async def test_account_deletion_cascades(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    assert (await _put(client, student)).status_code == 200
    with _connect(migrated_db) as conn:
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (student.id,))
    assert _rows(migrated_db, student.id) == []


def test_openapi_never_exposes_the_token_in_a_response(app) -> None:
    schema = app.openapi()["components"]["schemas"]
    assert set(schema["ProviderKeyMeta"]["properties"]) == {
        "provider",
        "has_key",
        "last4",
        "validated_at",
        "needs_reentry",
    }
    assert schema["ProviderKeyInput"]["properties"]["token"].get("writeOnly") is True


# --------------------------------------------------------------------------- review fixes


async def test_consent_revoked_during_validation(
    app,
    client: AsyncClient,
    student: Student,
    key_events: list[ProviderKeyChanged],
    migrated_db: str,
) -> None:
    class RevokingRunner(FakeProviderRunner):
        async def validate_token(self, token: SecretStr) -> ProviderAccount:
            with _connect(migrated_db) as conn:
                conn.execute(
                    "UPDATE app.consents SET revoked_at = now(), end_reason = 'revoked' "
                    "WHERE owner_id = %s AND purpose = 'provider_runs' AND revoked_at IS NULL",
                    (student.id,),
                )
            return await super().validate_token(token)

    runner = RevokingRunner()
    app.state.provider_runner = runner
    resp = await _put(client, student)
    assert resp.status_code == 403
    body = resp.json()
    assert body["type"] == "/problems/consent-required" and body["purpose"] == "provider_runs"
    assert runner.validated == [TOKEN]
    assert _rows(migrated_db, student.id) == [] and key_events == []


async def test_revoking_provider_runs_pauses_collection_and_keeps_the_key(
    client: AsyncClient, student: Student, schedules: list[uuid.UUID], migrated_db: str
) -> None:
    assert (await _put(client, student)).status_code == 200
    collect = _add_schedule(migrated_db, student.id, "listings.collect")
    other = _add_schedule(migrated_db, student.id, "matching.other_kind")
    schedules += [collect, other]

    resp = await client.delete("/api/v1/consents/pool_contribution", headers=student.headers)
    assert resp.status_code == 204
    assert _paused(migrated_db, collect) is False  # another purpose: untouched

    resp = await client.delete("/api/v1/consents/provider_runs", headers=student.headers)
    assert resp.status_code == 204
    assert _paused(migrated_db, collect) is True
    assert _paused(migrated_db, other) is False
    assert len(_rows(migrated_db, student.id)) == 1  # the key is kept


async def test_retired_key_version_needs_reentry(
    client: AsyncClient,
    student: Student,
    master_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.errors import ProviderKeyUnreadableError

    assert (await _put(client, student)).status_code == 200
    # Rotate to version 2 and drop version 1: the stored row is now unreadable.
    monkeypatch.setenv(
        "CREDENTIALS_MASTER_KEYS", "2:" + base64.b64encode(os.urandom(32)).decode()
    )
    get_settings.cache_clear()
    got = (await client.get(KEY_URL, headers=student.headers)).json()
    assert got["has_key"] is True and got["needs_reentry"] is True
    async with get_sessionmaker()() as session:
        with pytest.raises(ProviderKeyUnreadableError):
            await service.decrypt_credential(session, student.id)

    # Re-entering replaces it under version 2.
    resp = await _put(client, student, TOKEN_2)
    assert resp.status_code == 200 and resp.json()["needs_reentry"] is False
    async with get_sessionmaker()() as session:
        secret = await service.decrypt_credential(session, student.id)
    assert secret is not None and secret.get_secret_value() == TOKEN_2


async def test_upsert_with_a_mismatched_id_is_a_conflict(student: Student) -> None:
    from app.core.errors import ConflictError
    from app.modules.accounts import repo

    async with get_sessionmaker()() as session:
        await repo.upsert_credential(
            session,
            credential_id=uuid.uuid7(),
            owner_id=student.id,
            provider="apify",
            ciphertext=b"v1:x",
            key_version=1,
            last4="abcd",
            provider_user_id=None,
        )
        with pytest.raises(ConflictError):
            await repo.upsert_credential(
                session,
                credential_id=uuid.uuid7(),
                owner_id=student.id,
                provider="apify",
                ciphertext=b"v1:y",
                key_version=1,
                last4="efgh",
                provider_user_id=None,
            )
        await session.rollback()


def test_key_version_check_constraint(sync_conn: psycopg.Connection) -> None:
    (definition,) = sync_conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'provider_credentials_key_version_check'"
    ).fetchone()  # type: ignore[misc]
    assert "1" in definition and "32767" in definition
