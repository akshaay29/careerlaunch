"""FR-2 / AD-17: ``DELETE /api/v1/me`` deletes the account and all of its data.

Storage and Supabase Auth are in-memory fakes (tests never touch real Supabase).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient

from app.core import events
from app.core.config import to_libpq_url
from app.core.db import get_sessionmaker
from app.core.events import AccountDeleting, subscribe
from app.core.ops import record_audit, record_event
from app.main import create_app
from app.modules.accounts import router as accounts_router
from app.modules.accounts.consent_text import ConsentPurpose, current_text
from app.modules.accounts.tasks import PURGE_RETRY, delete_auth_user, purge_files
from app.ports.auth_admin import AuthAdminError
from app.ports.file_store import FileStoreError
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.auth_admin import FakeAuthAdmin
from tests.fakes.file_store import FakeFileStore

ME = "/api/v1/me"
PROBLEM = "application/problem+json"
PDF_BYTES = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
TASKS = ("accounts.purge_files", "accounts.delete_auth_user")

pytestmark = pytest.mark.usefixtures("clean_jobs")


@pytest.fixture
def fake_store() -> FakeFileStore:
    return FakeFileStore()


@pytest.fixture
def fake_auth() -> FakeAuthAdmin:
    return FakeAuthAdmin()


@pytest.fixture
def app(fake_store: FakeFileStore, fake_auth: FakeAuthAdmin):
    """Overrides conftest's ``app``: wired with the fake FileStore and AuthAdmin."""
    return create_app(file_store=fake_store, auth_admin=fake_auth)


@pytest.fixture(autouse=True)
def drop_tombstones(accounts: AccountFactory) -> Iterator[None]:
    yield
    if accounts.ids:
        with _connect(accounts.url) as conn:
            conn.execute("DELETE FROM app.deleted_accounts WHERE id = ANY(%s)", (accounts.ids,))


@pytest.fixture
def deleting() -> Iterator[list[tuple[AccountDeleting, bool]]]:
    """Record every AccountDeleting and whether the account row still existed when it was
    published; the subscriber registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[tuple[AccountDeleting, bool]] = []

    async def record(session: Any, event: AccountDeleting) -> None:
        from sqlalchemy import text

        exists = (
            await session.execute(
                text("SELECT 1 FROM accounts WHERE id = :id"), {"id": event.owner_id}
            )
        ).first() is not None
        seen.append((event, exists))

    subscribe(AccountDeleting, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


Student = tuple[uuid.UUID, str, dict[str, str]]


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url if url.startswith("postgresql://") else to_libpq_url(url), autocommit=True)


def _student(
    accounts: AccountFactory, tokens: TokenFactory, *, role: str = "student", email: str | None = None
) -> Student:
    account_id = accounts.create(role=role, email=email)
    _, stored_email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, stored_email, tokens.headers(tokens.token(account_id, stored_email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory, fake_auth: FakeAuthAdmin) -> Student:
    s = _student(accounts, tokens)
    fake_auth.users.add(s[0])
    return s


@pytest.fixture
def other_student(
    accounts: AccountFactory, tokens: TokenFactory, fake_auth: FakeAuthAdmin
) -> Student:
    s = _student(accounts, tokens)
    fake_auth.users.add(s[0])
    return s


def _owned_tables(url: str) -> list[str]:
    with _connect(url) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'app' AND column_name = 'owner_id' "
                "AND table_name <> 'ops_events' ORDER BY table_name"
            )
        ]


def _owned_counts(url: str, owner: uuid.UUID) -> dict[str, int]:
    with _connect(url) as conn:
        return {
            table: conn.execute(
                f'SELECT count(*) FROM app."{table}" WHERE owner_id = %s', (owner,)
            ).fetchone()[0]  # type: ignore[index]
            for table in _owned_tables(url)
        }


def _jobs(url: str) -> list[tuple]:
    with _connect(url) as conn:
        return conn.execute(
            "SELECT task_name, queueing_lock, status, args, queue_name "
            "FROM app.procrastinate_jobs WHERE task_name = ANY(%s) ORDER BY task_name",
            (list(TASKS),),
        ).fetchall()


def _ops(url: str, kind: str) -> list[tuple]:
    with _connect(url) as conn:
        return conn.execute(
            "SELECT owner_id, entity_type, entity_id, counts FROM app.ops_events WHERE kind = %s",
            (kind,),
        ).fetchall()


def _tombstone(url: str, owner: uuid.UUID) -> tuple | None:
    """(deleted_at, files_purged_at, auth_deleted_at) or None."""
    with _connect(url) as conn:
        return conn.execute(
            "SELECT deleted_at, files_purged_at, auth_deleted_at FROM app.deleted_accounts "
            "WHERE id = %s",
            (owner,),
        ).fetchone()


def _tombstoned(url: str, owner: uuid.UUID) -> bool:
    with _connect(url) as conn:
        return conn.execute(
            "SELECT 1 FROM app.deleted_accounts WHERE id = %s", (owner,)
        ).fetchone() is not None


async def _populate(client: AsyncClient, url: str, owner: uuid.UUID, headers: dict[str, str]) -> None:
    """Consents, a Resume (-> Skill Profile), a Document, Preferences and a schedule."""
    for purpose in ConsentPurpose:
        resp = await client.put(
            f"/api/v1/consents/{purpose.value}",
            json={"version": current_text(purpose).version},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    files = {"file": ("cv.pdf", PDF_BYTES, "application/pdf")}
    assert (await client.post("/api/v1/resumes", files=files, headers=headers)).status_code == 201
    files = {"file": ("cover.pdf", PDF_BYTES, "application/pdf")}
    assert (await client.post("/api/v1/documents", files=files, headers=headers)).status_code == 201
    prefs = {
        "role_titles": ["Backend Intern"],
        "locations": ["Remote"],
        "job_types": ["internship"],
        "batch_year": 2027,
        "cgpa": 8.5,
        "degree": "btech",
        "degree_other": None,
        "work_authorisation": "india_authorised",
        "threshold": 70,
        "alert_frequency": "daily",
        "timezone": "Asia/Kolkata",
    }
    assert (await client.put("/api/v1/preferences", json=prefs, headers=headers)).status_code == 200
    with _connect(url) as conn:
        conn.execute(
            'INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval") '
            "VALUES (%s, 'tests.deletion', %s, %s, interval '1 day')",
            (uuid.uuid7(), owner, datetime.now(UTC) + timedelta(days=1)),
        )


def _assert_problem(resp, status: int, slug: str) -> dict:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == f"/problems/{slug}"
    return body


# --------------------------------------------------------------------------- delete


async def test_delete_removes_everything(
    client: AsyncClient,
    student: Student,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    fake_auth: FakeAuthAdmin,
    migrated_db: str,
    deleting: list[tuple[AccountDeleting, bool]],
) -> None:
    owner, email, headers = student
    await _populate(client, migrated_db, owner, headers)
    before = _owned_counts(migrated_db, owner)
    for table in ("consents", "resumes", "skill_profiles", "preferences", "documents", "schedules"):
        assert before[table] >= 1, table
    assert len(fake_store.paths_under(f"{owner}/")) == 2

    resp = await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    assert accounts.row(owner) is None
    assert set(_owned_counts(migrated_db, owner).values()) == {0}
    assert _tombstoned(migrated_db, owner)
    # Published exactly once, before the account row was deleted.
    assert deleting == [(AccountDeleting(owner_id=owner), True)]

    # Both jobs: ids only, the account key, the priority queue; the after-response drain ran.
    jobs = _jobs(migrated_db)
    assert [(j[0], j[1], j[2], j[3], j[4]) for j in jobs] == [
        (
            "accounts.delete_auth_user",
            f"accounts.delete_auth_user:account:{owner}",
            "succeeded",
            {"owner_id": str(owner)},
            "priority",
        ),
        (
            "accounts.purge_files",
            f"accounts.purge_files:account:{owner}",
            "succeeded",
            {"owner_id": str(owner)},
            "priority",
        ),
    ]

    # Acceptance: no objects, no Auth user, both purge rows without owner or entity ids.
    assert fake_store.paths_under(f"{owner}/") == []
    assert owner not in fake_auth.users and fake_auth.deleted == [owner]
    assert _ops(migrated_db, "account.deleted") == [(None, None, None, {})]
    assert _ops(migrated_db, "account.files_purged") == [(None, None, None, {"objects": 2})]
    assert _ops(migrated_db, "account.auth_deleted") == [(None, None, None, {})]
    # Both jobs stamped the tombstone: the deletion is complete.
    assert None not in _tombstone(migrated_db, owner)  # type: ignore[operator]
    # Job bookkeeping for the deleted owner is kept, with the owner stored as NULL.
    with _connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT owner_id FROM app.ops_events WHERE kind = 'job.succeeded' "
            "AND entity_type = ANY(%s)",
            (list(TASKS),),
        ).fetchall()
    assert sorted(rows) == [(None,), (None,)]


async def test_email_mismatch_is_422_and_nothing_changes(
    client: AsyncClient,
    student: Student,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    migrated_db: str,
    deleting: list[tuple[AccountDeleting, bool]],
) -> None:
    owner, email, headers = student
    files = {"file": ("cover.pdf", PDF_BYTES, "application/pdf")}
    await client.post("/api/v1/documents", files=files, headers=headers)
    before = _owned_counts(migrated_db, owner)
    objects = dict(fake_store.objects)

    for wrong in ("someone-else@example.test", "", f"x{email}"):
        body = _assert_problem(
            await client.request("DELETE", ME, json={"confirm_email": wrong}, headers=headers),
            422,
            "validation-error",
        )
        assert body["errors"][0]["loc"] == ["body", "confirm_email"]
    # A missing field is a 422 on the same field.
    body = _assert_problem(
        await client.request("DELETE", ME, json={}, headers=headers), 422, "validation-error"
    )
    assert body["errors"][0]["loc"][-1] == "confirm_email"

    assert accounts.row(owner) is not None
    assert _owned_counts(migrated_db, owner) == before
    assert not _tombstoned(migrated_db, owner)
    assert deleting == []
    assert _jobs(migrated_db) == []
    assert fake_store.objects == objects
    assert _ops(migrated_db, "account.deleted") == []
    with _connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM app.job_outbox").fetchone() == (0,)


async def test_confirmation_ignores_case_and_surrounding_spaces(
    client: AsyncClient, accounts: AccountFactory, tokens: TokenFactory, migrated_db: str
) -> None:
    owner, email, headers = _student(
        accounts, tokens, email=f"student-{uuid.uuid4().hex[:8]}@x.com"
    )
    resp = await client.request(
        "DELETE", ME, json={"confirm_email": f"  {email.upper()} "}, headers=headers
    )
    assert resp.status_code == 204, resp.text
    assert accounts.row(owner) is None


async def test_admin_can_delete_themselves(
    client: AsyncClient, accounts: AccountFactory, tokens: TokenFactory
) -> None:
    owner, email, headers = _student(accounts, tokens, role="admin")
    resp = await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)
    assert resp.status_code == 204, resp.text
    assert accounts.row(owner) is None


# --------------------------------------------------------------------------- jobs


async def test_purge_removes_only_the_owners_objects_and_reruns_remove_nothing(
    fake_store: FakeFileStore, app: Any, migrated_db: str
) -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()
    for path in (
        f"{owner}/resume/{uuid.uuid7()}",
        f"{owner}/resume/{uuid.uuid7()}",
        f"{owner}/document/{uuid.uuid7()}",
        f"{other}/resume/{uuid.uuid7()}",
    ):
        await fake_store.put(path, b"x", "application/pdf")
    await purge_files(owner_id=owner)
    assert fake_store.paths_under(f"{owner}/") == []
    assert len(fake_store.paths_under(f"{other}/")) == 1
    await purge_files(owner_id=owner)  # at-least-once delivery: harmless
    assert _ops(migrated_db, "account.files_purged") == [
        (None, None, None, {"objects": 3}),
        (None, None, None, {"objects": 0}),
    ]


async def test_storage_down_makes_the_purge_retry(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
) -> None:
    from app.core.jobs import drain

    owner, email, headers = student
    files = {"file": ("cover.pdf", PDF_BYTES, "application/pdf")}
    await client.post("/api/v1/documents", files=files, headers=headers)
    fake_store.fail_delete = True
    resp = await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)
    assert resp.status_code == 204
    jobs = {j[0]: j[2] for j in _jobs(migrated_db)}
    assert jobs["accounts.purge_files"] == "todo"  # failed attempt, retry scheduled
    assert jobs["accounts.delete_auth_user"] == "succeeded"
    assert fake_store.paths_under(f"{owner}/")
    assert _ops(migrated_db, "account.files_purged") == []

    with pytest.raises(FileStoreError, match="fake delete failure"):
        await purge_files(owner_id=owner)

    fake_store.fail_delete = False
    with _connect(migrated_db) as conn:
        conn.execute("SET search_path TO app")  # Procrastinate's triggers use unqualified names
        conn.execute(
            "UPDATE app.procrastinate_jobs SET scheduled_at = now() "
            "WHERE task_name = 'accounts.purge_files'"
        )
    await drain(queues=("priority",))
    assert fake_store.paths_under(f"{owner}/") == []
    assert {j[0]: j[2] for j in _jobs(migrated_db)}["accounts.purge_files"] == "succeeded"
    assert _ops(migrated_db, "account.files_purged") == [(None, None, None, {"objects": 1})]
    # A long retry policy that spans days.
    assert PURGE_RETRY.max_attempts >= 10 and PURGE_RETRY.exponential_wait ** (
        PURGE_RETRY.max_attempts - 1
    ) > 2 * 24 * 3600


async def test_delete_auth_user_records_event_and_is_idempotent(
    fake_auth: FakeAuthAdmin, app: Any, migrated_db: str
) -> None:
    user = uuid.uuid4()
    fake_auth.users.add(user)
    await delete_auth_user(owner_id=user)
    assert user not in fake_auth.users
    await delete_auth_user(owner_id=user)
    assert _ops(migrated_db, "account.auth_deleted") == [(None, None, None, {})] * 2

    fake_auth.fail_delete = True
    with pytest.raises(AuthAdminError, match="fake delete_user failure"):
        await delete_auth_user(owner_id=user)
    assert len(_ops(migrated_db, "account.auth_deleted")) == 2


# --------------------------------------------------------------------------- tokens, globals


async def test_stale_token_is_401_and_does_not_recreate_the_account(
    client: AsyncClient, student: Student, accounts: AccountFactory
) -> None:
    owner, email, headers = student
    assert (await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)).status_code == 204
    for method, path in (("GET", ME), ("GET", "/api/v1/consents"), ("DELETE", ME)):
        kwargs = {"json": {"confirm_email": email}} if method == "DELETE" else {}
        _assert_problem(await client.request(method, path, headers=headers, **kwargs), 401, "unauthorized")
    assert accounts.row(owner) is None


async def test_global_rows_remain_with_null_references(
    client: AsyncClient, student: Student, accounts: AccountFactory, migrated_db: str
) -> None:
    owner, email, headers = student
    admin = accounts.create(role="admin")
    async with get_sessionmaker()() as session:
        audit_id = await record_audit(
            session, "account.test_action", actor_id=admin, target_account_id=owner
        )
        event_id = await record_event(session, "x.test", owner_id=owner, counts={"n": 1})
        await session.commit()
    assert (await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)).status_code == 204
    with _connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT actor_id, target_account_id FROM app.admin_audit WHERE id = %s", (audit_id,)
        ).fetchone() == (admin, None)
        assert conn.execute(
            "SELECT owner_id, counts FROM app.ops_events WHERE id = %s", (event_id,)
        ).fetchone() == (None, {"n": 1})


async def test_no_token_is_401(client: AsyncClient, student: Student, accounts: AccountFactory) -> None:
    owner, email, _ = student
    _assert_problem(await client.request("DELETE", ME, json={"confirm_email": email}), 401, "unauthorized")
    assert accounts.row(owner) is not None


async def test_other_students_are_untouched(
    client: AsyncClient,
    student: Student,
    other_student: Student,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    fake_auth: FakeAuthAdmin,
    migrated_db: str,
) -> None:
    owner, email, headers = student
    other, _, other_headers = other_student
    await _populate(client, migrated_db, other, other_headers)
    before = _owned_counts(migrated_db, other)
    other_objects = fake_store.paths_under(f"{other}/")
    assert other_objects

    assert (await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)).status_code == 204
    assert accounts.row(other) is not None
    assert _owned_counts(migrated_db, other) == before
    assert fake_store.paths_under(f"{other}/") == other_objects
    assert other in fake_auth.users
    assert not _tombstoned(migrated_db, other)
    assert (await client.get(ME, headers=other_headers)).status_code == 200


async def test_router_starts_the_priority_drain(
    client: AsyncClient, student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    monkeypatch.setattr(accounts_router, "drain_priority", counted)
    owner, email, headers = student
    assert (await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)).status_code == 204
    assert calls == [1]


# --------------------------------------------------------------------------- concurrency


async def test_second_concurrent_delete_is_already_deleted(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    """A second DELETE that loaded its actor before the first committed finds the row gone
    and the tombstone present: it reports success and writes nothing (no new jobs)."""
    from app.modules.accounts import service

    owner, email, headers = student
    assert (
        await client.request("DELETE", ME, json={"confirm_email": email}, headers=headers)
    ).status_code == 204
    jobs_before = _jobs(migrated_db)
    async with get_sessionmaker()() as session:
        assert await service.delete_account(session, owner, email) is False
        await session.commit()
    with _connect(migrated_db) as conn:
        assert conn.execute("SELECT count(*) FROM app.job_outbox").fetchone() == (0,)
    assert _jobs(migrated_db) == jobs_before
    assert len(_ops(migrated_db, "account.deleted")) == 1


async def test_delete_of_an_unknown_account_without_tombstone_is_404() -> None:
    from app.core.errors import NotFoundError
    from app.modules.accounts import service

    async with get_sessionmaker()() as session:
        with pytest.raises(NotFoundError):
            await service.delete_account(session, uuid.uuid4(), "x@example.test")


# --------------------------------------------------------------------------- backstop


def _insert_tombstone(
    url: str, owner: uuid.UUID, age: timedelta, files_done: bool, auth_done: bool
) -> None:
    deleted_at = datetime.now(UTC) - age
    with _connect(url) as conn:
        conn.execute(
            "INSERT INTO app.deleted_accounts (id, deleted_at, files_purged_at, auth_deleted_at) "
            "VALUES (%s, %s, %s, %s)",
            (
                owner,
                deleted_at,
                deleted_at if files_done else None,
                deleted_at if auth_done else None,
            ),
        )


def _outbox(url: str) -> list[tuple]:
    with _connect(url) as conn:
        return conn.execute(
            "SELECT task, key, queue, args FROM app.job_outbox ORDER BY key"
        ).fetchall()


async def test_resume_pending_deletions_re_enqueues_only_what_is_missing(
    app: Any, accounts: AccountFactory, migrated_db: str
) -> None:
    from app.modules.accounts.tasks import resume_pending_deletions

    pending = accounts.track(uuid.uuid4())  # nothing done
    half = accounts.track(uuid.uuid4())  # files done, Auth user still there
    done = accounts.track(uuid.uuid4())  # complete
    recent = accounts.track(uuid.uuid4())  # under an hour old: left to its own jobs
    _insert_tombstone(migrated_db, pending, timedelta(days=5), False, False)
    _insert_tombstone(migrated_db, half, timedelta(hours=2), True, False)
    _insert_tombstone(migrated_db, done, timedelta(days=5), True, True)
    _insert_tombstone(migrated_db, recent, timedelta(minutes=5), False, False)

    await resume_pending_deletions()

    assert _outbox(migrated_db) == sorted(
        [
            (
                "accounts.delete_auth_user",
                f"accounts.delete_auth_user:account:{pending}",
                "priority",
                {"owner_id": str(pending)},
            ),
            (
                "accounts.purge_files",
                f"accounts.purge_files:account:{pending}",
                "priority",
                {"owner_id": str(pending)},
            ),
            (
                "accounts.delete_auth_user",
                f"accounts.delete_auth_user:account:{half}",
                "priority",
                {"owner_id": str(half)},
            ),
        ],
        key=lambda r: r[1],
    )


async def test_resumed_jobs_complete_the_deletion(
    app: Any,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    fake_auth: FakeAuthAdmin,
    migrated_db: str,
) -> None:
    from app.core.jobs import drain
    from app.modules.accounts.tasks import resume_pending_deletions

    owner = accounts.track(uuid.uuid4())
    fake_auth.users.add(owner)
    await fake_store.put(f"{owner}/resume/{uuid.uuid7()}", b"x", "application/pdf")
    _insert_tombstone(migrated_db, owner, timedelta(days=5), False, False)

    await resume_pending_deletions()
    await drain(queues=("priority",))

    assert fake_store.paths_under(f"{owner}/") == []
    assert owner not in fake_auth.users
    assert None not in _tombstone(migrated_db, owner)  # type: ignore[operator]
    # Complete now: the next daily run enqueues nothing for it.
    await resume_pending_deletions()
    assert _outbox(migrated_db) == []


def test_backstop_schedule_row_exists_after_migration(migrated_db: str) -> None:
    from alembic import command

    from tests.conftest import alembic_config

    # clean_jobs truncated schedules; re-run 0010 to see what the migration inserts.
    cfg = alembic_config()
    command.downgrade(cfg, "0009_documents")
    command.upgrade(cfg, "head")
    with _connect(migrated_db) as conn:
        rows = conn.execute(
            'SELECT owner_id, paused, "interval", next_run_at > now() FROM app.schedules '
            "WHERE kind = 'accounts.resume_pending_deletions'"
        ).fetchall()
    assert rows == [(None, False, timedelta(days=1), True)]
