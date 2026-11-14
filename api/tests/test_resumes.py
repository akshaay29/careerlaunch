"""FR-3 / Files convention: Resume upload, list, activate, download and delete.

Storage is the in-memory FakeFileStore (tests never touch real Storage).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import get_settings, to_libpq_url
from app.core.events import ResumeActivated, subscribe
from app.main import create_app
from app.modules.profile import repo as profile_repo
from app.modules.profile.files import DOCX, MAX_FILE_BYTES, PDF
from app.ports import file_store as file_store_port
from app.ports.file_store import LazyFileStore, get_file_store
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.file_store import FakeFileStore
from tests.test_file_sniffing import PNG, make_docx

RESUMES = "/api/v1/resumes"
PROBLEM = "application/problem+json"
PDF_BYTES = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"

pytestmark = pytest.mark.usefixtures("clean_jobs")


@pytest.fixture
def fake_store() -> FakeFileStore:
    return FakeFileStore()


@pytest.fixture
def app(fake_store: FakeFileStore):
    """Overrides conftest's ``app``: the same app wired with the fake FileStore."""
    return create_app(file_store=fake_store)


Student = tuple[uuid.UUID, dict[str, str]]


def _student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, tokens.headers(tokens.token(account_id, email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def other_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def activated() -> Iterator[list[ResumeActivated]]:
    """Record every ResumeActivated; the subscriber registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[ResumeActivated] = []

    async def record(session: Any, event: ResumeActivated) -> None:
        seen.append(event)

    subscribe(ResumeActivated, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


def _rows(url: str, owner_id: uuid.UUID) -> list[tuple]:
    """(id, filename, content_type, size_bytes, is_active), newest first."""
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT id, filename, content_type, size_bytes, is_active FROM app.resumes "
            "WHERE owner_id = %s ORDER BY id DESC",
            (owner_id,),
        ).fetchall()


def _jobs(url: str) -> list[tuple]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT task_name, queueing_lock, status, args FROM app.procrastinate_jobs "
            "ORDER BY id"
        ).fetchall()


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    data: bytes = PDF_BYTES,
    filename: str = "cv.pdf",
    content_type: str = "application/pdf",
):
    return await client.post(
        RESUMES, files={"file": (filename, data, content_type)}, headers=headers
    )


def _assert_problem(resp, status: int, slug: str, detail: str | None = None) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == f"/problems/{slug}"
    if detail is not None:
        assert body["detail"] == detail


# --------------------------------------------------------------------------- upload


async def test_first_upload_is_stored_active_and_publishes(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    activated: list[ResumeActivated],
    migrated_db: str,
) -> None:
    owner, headers = student
    resp = await _upload(client, headers, filename="C:\\fakepath\\My CV.pdf")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    resume_id = uuid.UUID(body["id"])
    assert resume_id.version == 7
    assert body["filename"] == "My CV.pdf"
    assert body["content_type"] == PDF
    assert body["size_bytes"] == len(PDF_BYTES)
    assert body["is_active"] is True

    path = f"{owner}/resume/{resume_id}"
    assert fake_store.paths_under(f"{owner}/") == [path]
    assert fake_store.objects[path].data == PDF_BYTES
    assert fake_store.objects[path].content_type == PDF
    assert _rows(migrated_db, owner) == [(resume_id, "My CV.pdf", PDF, len(PDF_BYTES), True)]
    assert activated == [ResumeActivated(owner_id=owner, resume_id=resume_id)]


async def test_later_upload_is_inactive_without_event(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    activated: list[ResumeActivated],
    migrated_db: str,
) -> None:
    owner, headers = student
    first = (await _upload(client, headers)).json()
    resp = await _upload(client, headers, make_docx(), "cv.docx", "application/octet-stream")
    assert resp.status_code == 201, resp.text
    second = resp.json()
    assert second["content_type"] == DOCX and second["is_active"] is False
    assert len(activated) == 1  # only the first upload
    rows = _rows(migrated_db, owner)
    assert [(str(r[0]), r[4]) for r in rows] == [(second["id"], False), (first["id"], True)]
    assert len(fake_store.paths_under(f"{owner}/resume/")) == 2


async def test_upload_after_deleting_the_active_one_becomes_active(
    client: AsyncClient,
    student: Student,
    activated: list[ResumeActivated],
    migrated_db: str,
) -> None:
    owner, headers = student
    first = (await _upload(client, headers)).json()
    second = (await _upload(client, headers)).json()
    assert (await client.delete(f"{RESUMES}/{first['id']}", headers=headers)).status_code == 204
    assert [r[4] for r in _rows(migrated_db, owner)] == [False]  # none active

    third = (await _upload(client, headers)).json()
    assert third["is_active"] is True
    assert [e.resume_id for e in activated] == [uuid.UUID(first["id"]), uuid.UUID(third["id"])]
    assert {str(r[0]): r[4] for r in _rows(migrated_db, owner)} == {
        second["id"]: False,
        third["id"]: True,
    }


async def test_exactly_5_mib_is_accepted(client: AsyncClient, student: Student) -> None:
    _, headers = student
    data = PDF_BYTES + b"0" * (MAX_FILE_BYTES - len(PDF_BYTES))
    resp = await _upload(client, headers, data)
    assert resp.status_code == 201, resp.text
    assert resp.json()["size_bytes"] == MAX_FILE_BYTES


async def test_too_big_is_413_and_nothing_stored(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    data = PDF_BYTES + b"0" * (MAX_FILE_BYTES + 1 - len(PDF_BYTES))
    resp = await _upload(client, headers, data)
    _assert_problem(resp, 413, "payload-too-large", "Resume must be 5 MB or smaller")
    assert fake_store.puts == 0 and fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (PNG, "cv.pdf", "application/pdf"),
        (make_docx(document=False), "cv.docx", DOCX),
        (b"", "cv.pdf", "application/pdf"),
        (b"plain text resume", "cv.txt", "text/plain"),
    ],
    ids=["png-renamed-pdf", "zip-without-document", "empty", "text"],
)
async def test_wrong_type_is_415_and_nothing_stored(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    data: bytes,
    filename: str,
    content_type: str,
) -> None:
    owner, headers = student
    resp = await _upload(client, headers, data, filename, content_type)
    _assert_problem(resp, 415, "unsupported-media-type", "Upload a PDF or DOCX file")
    assert fake_store.puts == 0
    assert _rows(migrated_db, owner) == []


async def test_missing_file_field_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.post(RESUMES, data={"other": "x"}, headers=headers)
    _assert_problem(resp, 422, "validation-error")


async def test_version_cap_is_409_and_nothing_stored(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESUME_MAX_VERSIONS", "2")
    get_settings.cache_clear()
    try:
        owner, headers = student
        for _ in range(2):
            assert (await _upload(client, headers)).status_code == 201
        resp = await _upload(client, headers)
        _assert_problem(resp, 409, "conflict", "Delete an old version first")
        assert fake_store.puts == 2
        assert len(_rows(migrated_db, owner)) == 2
    finally:
        monkeypatch.delenv("RESUME_MAX_VERSIONS")
        get_settings.cache_clear()


async def test_default_version_cap_is_10() -> None:
    assert get_settings().resume_max_versions == 10


async def test_db_failure_after_store_deletes_the_object(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, headers = student

    async def failing_insert(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("insert failed")

    monkeypatch.setattr(profile_repo, "insert_resume", failing_insert)
    resp = await _upload(client, headers)
    _assert_problem(resp, 500, "internal-error")
    assert fake_store.puts == 1  # it was stored first ...
    assert fake_store.objects == {}  # ... and deleted again
    assert _rows(migrated_db, owner) == []


async def test_subscriber_failure_rolls_back_and_deletes_the_object(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    activated: list[ResumeActivated],
) -> None:
    owner, headers = student

    async def boom(session: Any, event: ResumeActivated) -> None:
        raise RuntimeError("subscriber failed")

    subscribe(ResumeActivated, boom)
    resp = await _upload(client, headers)
    _assert_problem(resp, 500, "internal-error")
    assert fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


async def test_store_failure_is_500_and_no_row(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    fake_store.fail_put = True
    _assert_problem(await _upload(client, headers), 500, "internal-error")
    assert _rows(migrated_db, owner) == []


async def test_put_failing_after_write_still_deletes_the_object(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    fake_store.fail_put_after_write = True  # e.g. a read timeout after Storage stored it
    _assert_problem(await _upload(client, headers), 500, "internal-error")
    assert fake_store.puts == 1
    assert fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


async def test_commit_failure_deletes_the_object(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, headers = student

    async def failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    resp = await _upload(client, headers)
    monkeypatch.undo()
    _assert_problem(resp, 500, "internal-error")
    assert fake_store.puts == 1 and fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


async def test_oversize_content_length_is_413_before_the_body_is_read(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    _, headers = student
    declared = MAX_FILE_BYTES + 64 * 1024 + 1
    resp = await client.post(
        RESUMES,
        content=b"--x--",
        headers={
            **headers,
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(declared),
        },
    )
    _assert_problem(resp, 413, "payload-too-large", "Resume must be 5 MB or smaller")
    assert fake_store.puts == 0


async def test_oversize_body_without_content_length_is_413(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    _, headers = student
    chunk = b"0" * (1024 * 1024)

    async def body() -> AsyncIterator[bytes]:
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="cv.pdf"\r\n\r\n%PDF-'
        for _ in range(7):  # well past 5 MB + 64 KiB
            yield chunk

    resp = await client.post(
        RESUMES,
        content=body(),
        headers={**headers, "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert "content-length" not in {k.lower() for k in resp.request.headers}
    _assert_problem(resp, 413, "payload-too-large", "Resume must be 5 MB or smaller")
    assert fake_store.puts == 0


async def test_concurrent_first_uploads_activate_exactly_one(
    client: AsyncClient,
    student: Student,
    migrated_db: str,
    activated: list[ResumeActivated],
) -> None:
    owner, headers = student
    results = await asyncio.gather(_upload(client, headers), _upload(client, headers))
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    rows = _rows(migrated_db, owner)
    assert len(rows) == 2 and [r[4] for r in rows].count(True) == 1
    assert len(activated) == 1
    assert activated[0].resume_id == next(r[0] for r in rows if r[4])


async def test_concurrent_uploads_at_the_cap_admit_one(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESUME_MAX_VERSIONS", "2")
    get_settings.cache_clear()
    try:
        owner, headers = student
        assert (await _upload(client, headers)).status_code == 201  # cap - 1 versions
        results = await asyncio.gather(_upload(client, headers), _upload(client, headers))
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        assert len(_rows(migrated_db, owner)) == 2
        assert len(fake_store.paths_under(f"{owner}/")) == 2
    finally:
        monkeypatch.delenv("RESUME_MAX_VERSIONS")
        get_settings.cache_clear()


# --------------------------------------------------------------------------- list


async def test_list_is_newest_first_with_cursor_pagination(
    client: AsyncClient, student: Student, other_student: Student
) -> None:
    _, headers = student
    ids = [(await _upload(client, headers)).json()["id"] for _ in range(3)]
    await _upload(client, other_student[1])  # never visible to `student`

    resp = await client.get(RESUMES, params={"limit": 2}, headers=headers)
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert [i["id"] for i in page["items"]] == [ids[2], ids[1]]
    assert page["next_cursor"] == ids[1]

    resp = await client.get(RESUMES, params={"limit": 2, "cursor": page["next_cursor"]}, headers=headers)
    page = resp.json()
    assert [i["id"] for i in page["items"]] == [ids[0]]
    assert page["next_cursor"] is None

    page = (await client.get(RESUMES, headers=headers)).json()
    assert [i["id"] for i in page["items"]] == ids[::-1]
    assert page["next_cursor"] is None
    assert [i["is_active"] for i in page["items"]] == [False, False, True]


@pytest.mark.parametrize("limit", [0, 51])
async def test_list_limit_bounds(client: AsyncClient, student: Student, limit: int) -> None:
    _assert_problem(
        await client.get(RESUMES, params={"limit": limit}, headers=student[1]), 422, "validation-error"
    )


async def test_list_empty(client: AsyncClient, student: Student) -> None:
    assert (await client.get(RESUMES, headers=student[1])).json() == {"items": [], "next_cursor": None}


# --------------------------------------------------------------------------- activate


async def test_activate_switches_and_publishes_once_before_commit(
    client: AsyncClient, student: Student, migrated_db: str, activated: list[ResumeActivated]
) -> None:
    owner, headers = student
    first = (await _upload(client, headers)).json()
    second = (await _upload(client, headers)).json()
    seen_in_session: list[dict[str, bool]] = []
    committed_view: list[dict[str, bool]] = []

    async def inspect(session: Any, event: ResumeActivated) -> None:
        rows = await session.execute(
            text("SELECT id, is_active FROM resumes WHERE owner_id = :o"), {"o": event.owner_id}
        )
        seen_in_session.append({str(r.id): r.is_active for r in rows})
        committed_view.append({str(r[0]): r[4] for r in _rows(migrated_db, event.owner_id)})

    subscribe(ResumeActivated, inspect)
    resp = await client.post(f"{RESUMES}/{second['id']}/activate", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == second["id"] and resp.json()["is_active"] is True

    assert activated[-1] == ResumeActivated(owner_id=owner, resume_id=uuid.UUID(second["id"]))
    assert len(activated) == 2  # first upload + this activation
    # The subscriber saw the new state in the publisher's session ...
    assert seen_in_session == [{first["id"]: False, second["id"]: True}]
    # ... while it was not yet committed.
    assert committed_view == [{first["id"]: True, second["id"]: False}]
    assert {str(r[0]): r[4] for r in _rows(migrated_db, owner)} == {
        first["id"]: False,
        second["id"]: True,
    }


async def test_activate_active_is_200_without_event(
    client: AsyncClient, student: Student, activated: list[ResumeActivated]
) -> None:
    _, headers = student
    first = (await _upload(client, headers)).json()
    assert len(activated) == 1
    resp = await client.post(f"{RESUMES}/{first['id']}/activate", headers=headers)
    assert resp.status_code == 200 and resp.json()["is_active"] is True
    assert len(activated) == 1


async def test_activate_when_none_active(
    client: AsyncClient, student: Student, migrated_db: str, activated: list[ResumeActivated]
) -> None:
    owner, headers = student
    first = (await _upload(client, headers)).json()
    second = (await _upload(client, headers)).json()
    await client.delete(f"{RESUMES}/{first['id']}", headers=headers)
    resp = await client.post(f"{RESUMES}/{second['id']}/activate", headers=headers)
    assert resp.status_code == 200
    assert [r[4] for r in _rows(migrated_db, owner)] == [True]
    assert activated[-1].resume_id == uuid.UUID(second["id"])


# --------------------------------------------------------------------------- download


async def test_download_is_short_lived_attachment_url(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    owner, headers = student
    created = (await _upload(client, headers, filename="My CV.pdf")).json()
    resp = await client.get(f"{RESUMES}/{created['id']}/download", headers=headers)
    after = datetime.now(UTC)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expires_at = datetime.fromisoformat(body["expires_at"])
    # Issued during the request: at most 300 s from now, and still in the future.
    assert 0 < (expires_at - after).total_seconds() <= 300
    (signed,) = fake_store.signed
    assert signed.path == f"{owner}/resume/{created['id']}"
    assert signed.expires_in_seconds <= 300
    assert signed.download_name == "My CV.pdf"
    assert body["url"] == signed.url and "download=My%20CV.pdf" in body["url"]
    # The bytes never pass through the api.
    assert PDF_BYTES not in resp.content


# --------------------------------------------------------------------------- delete


async def test_delete_removes_row_and_object_via_job(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    keep = (await _upload(client, headers)).json()
    gone = (await _upload(client, headers)).json()
    resp = await client.delete(f"{RESUMES}/{gone['id']}", headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    assert [str(r[0]) for r in _rows(migrated_db, owner)] == [keep["id"]]
    # The after-response priority drain ran the delete job.
    jobs = _jobs(migrated_db)
    assert [(j[0], j[1], j[2]) for j in jobs] == [
        (
            "profile.delete_stored_files",
            f"profile.delete_stored_files:resume:{gone['id']}",
            "succeeded",
        )
    ]
    assert jobs[0][3] == {"owner_id": str(owner), "resume_id": gone["id"]}  # ids only
    assert fake_store.paths_under(f"{owner}/") == [f"{owner}/resume/{keep['id']}"]

    # Gone means gone.
    _assert_problem(await client.delete(f"{RESUMES}/{gone['id']}", headers=headers), 404, "not-found")


async def test_delete_active_leaves_none_active(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    owner, headers = student
    active = (await _upload(client, headers)).json()
    await _upload(client, headers)
    assert (await client.delete(f"{RESUMES}/{active['id']}", headers=headers)).status_code == 204
    assert [r[4] for r in _rows(migrated_db, owner)] == [False]


async def test_delete_job_is_idempotent_and_survives_storage_outage(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    from app.core.jobs import drain
    from app.modules.profile.tasks import delete_stored_files

    owner, headers = student
    created = (await _upload(client, headers)).json()
    fake_store.fail_delete = True
    assert (await client.delete(f"{RESUMES}/{created['id']}", headers=headers)).status_code == 204
    # The row is gone even though Storage failed; the job is kept for a retry.
    assert _rows(migrated_db, owner) == []
    assert fake_store.paths_under(f"{owner}/")
    assert _jobs(migrated_db)[0][2] == "todo"  # failed attempt, retry scheduled

    fake_store.fail_delete = False
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("SET search_path TO app")  # Procrastinate's triggers expect it
        conn.execute("UPDATE procrastinate_jobs SET scheduled_at = now()")
    await drain(queues=("priority",))
    assert fake_store.paths_under(f"{owner}/") == []
    assert _jobs(migrated_db)[0][2] == "succeeded"
    # Running it again (at-least-once delivery) is harmless.
    await delete_stored_files(owner_id=owner, resume_id=uuid.UUID(created["id"]))


# --------------------------------------------------------------------------- isolation


async def test_other_students_id_is_404_everywhere_and_nothing_changes(
    client: AsyncClient,
    student: Student,
    other_student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
) -> None:
    owner, headers = student
    _, intruder = other_student
    mine = (await _upload(client, headers)).json()
    await _upload(client, headers)  # inactive second version
    second = _rows(migrated_db, owner)[0][0]
    before = _rows(migrated_db, owner)

    for target in (mine["id"], str(second), str(uuid.uuid7())):
        _assert_problem(await client.post(f"{RESUMES}/{target}/activate", headers=intruder), 404, "not-found")
        _assert_problem(await client.get(f"{RESUMES}/{target}/download", headers=intruder), 404, "not-found")
        _assert_problem(await client.delete(f"{RESUMES}/{target}", headers=intruder), 404, "not-found")

    assert _rows(migrated_db, owner) == before
    assert fake_store.signed == [] and fake_store.deleted == []
    assert _jobs(migrated_db) == []


async def test_unknown_id_is_404_for_owner(client: AsyncClient, student: Student) -> None:
    _, headers = student
    unknown = uuid.uuid7()
    _assert_problem(await client.get(f"{RESUMES}/{unknown}/download", headers=headers), 404, "not-found")


async def test_no_token_is_401_everywhere(client: AsyncClient, fake_store: FakeFileStore) -> None:
    rid = uuid.uuid7()
    for method, path in [
        ("POST", RESUMES),
        ("GET", RESUMES),
        ("POST", f"{RESUMES}/{rid}/activate"),
        ("GET", f"{RESUMES}/{rid}/download"),
        ("DELETE", f"{RESUMES}/{rid}"),
    ]:
        kwargs: dict[str, Any] = {}
        if method == "POST" and path == RESUMES:
            kwargs["files"] = {"file": ("cv.pdf", PDF_BYTES, "application/pdf")}
        resp = await client.request(method, path, **kwargs)
        _assert_problem(resp, 401, "unauthorized")
    assert fake_store.puts == 0


async def test_account_deletion_cascades_resumes(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    owner, headers = student
    await _upload(client, headers)
    await _upload(client, headers)
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert _rows(migrated_db, owner) == []


# --------------------------------------------------------------------------- schema / wiring


def test_one_active_per_owner_is_enforced_by_the_db(
    sync_conn: psycopg.Connection, accounts: AccountFactory
) -> None:
    owner = accounts.create()
    insert = (
        "INSERT INTO app.resumes (id, owner_id, filename, content_type, size_bytes, sha256, "
        "is_active) VALUES (%s, %s, 'cv.pdf', 'application/pdf', 1, %s, true)"
    )
    sync_conn.execute(insert, (uuid.uuid7(), owner, "0" * 64))
    with pytest.raises(psycopg.errors.UniqueViolation):
        sync_conn.execute(insert, (uuid.uuid7(), owner, "0" * 64))


def test_resumes_table_has_forced_rls_and_no_exposed_grants(sync_conn: psycopg.Connection) -> None:
    (rls, forced) = sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'app.resumes'::regclass"
    ).fetchone()  # type: ignore[misc]
    assert rls and forced
    (grants,) = sync_conn.execute(
        "SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema = 'app' "
        "AND table_name = 'resumes' AND grantee IN ('anon', 'authenticated', 'PUBLIC')"
    ).fetchone()  # type: ignore[misc]
    assert grants == 0


def test_app_wires_the_injected_store(app, fake_store: FakeFileStore) -> None:
    assert app.state.file_store is fake_store
    assert get_file_store() is fake_store


def test_default_app_builds_storage_lazily() -> None:
    # Creating the app (OpenAPI export) needs no service-role key.
    previous = file_store_port._installed
    try:
        app = create_app()
        assert isinstance(app.state.file_store, LazyFileStore)
    finally:
        file_store_port.install_file_store(previous)  # type: ignore[arg-type]
