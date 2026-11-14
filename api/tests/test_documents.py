"""FR-26 / Files convention: Document upload, list, download and delete.

Storage is the in-memory FakeFileStore (tests never touch real Storage).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings, to_libpq_url
from app.main import create_app
from app.modules.profile import repo as profile_repo
from app.modules.profile import service as profile_service
from app.modules.profile.files import DOCX, MAX_FILE_BYTES, PDF, TEXT
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.file_store import FakeFileStore
from tests.test_file_sniffing import PNG, make_docx

DOCUMENTS = "/api/v1/documents"
PROBLEM = "application/problem+json"
PDF_BYTES = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
TEXT_BYTES = "Dear hiring manager,\n\tThank you — I look forward to it.\r\n".encode()
BOM = b"\xef\xbb\xbf"

TOO_LARGE = "Document must be 5 MB or smaller"
WRONG_TYPE = "Upload a PDF, DOCX or plain-text file"

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
def published(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every event the profile service publishes (Documents must publish none)."""
    seen: list[Any] = []
    original = profile_service.publish

    async def recording_publish(session: Any, event: Any) -> None:
        seen.append(event)
        await original(session, event)

    monkeypatch.setattr(profile_service, "publish", recording_publish)
    return seen


def _rows(url: str, owner_id: uuid.UUID) -> list[tuple]:
    """(id, filename, content_type, size_bytes), newest first."""
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT id, filename, content_type, size_bytes FROM app.documents "
            "WHERE owner_id = %s ORDER BY id DESC",
            (owner_id,),
        ).fetchall()


def _jobs(url: str, task: str = "profile.delete_document_file") -> list[tuple]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT task_name, queueing_lock, status, args, queue_name "
            "FROM app.procrastinate_jobs WHERE task_name = %s ORDER BY id",
            (task,),
        ).fetchall()


def _all_jobs(url: str) -> list[tuple]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute("SELECT task_name FROM app.procrastinate_jobs").fetchall()


async def _upload(
    client: AsyncClient,
    headers: dict[str, str],
    data: bytes = PDF_BYTES,
    filename: str = "cover.pdf",
    content_type: str = "application/pdf",
):
    return await client.post(
        DOCUMENTS, files={"file": (filename, data, content_type)}, headers=headers
    )


def _assert_problem(resp, status: int, slug: str, detail: str | None = None) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == f"/problems/{slug}"
    if detail is not None:
        assert body["detail"] == detail


# --------------------------------------------------------------------------- upload


@pytest.mark.parametrize(
    ("data", "filename", "expected"),
    [
        (PDF_BYTES, "C:\\fakepath\\Cover Letter.pdf", PDF),
        (make_docx(), "transcript.docx", DOCX),
        (TEXT_BYTES, "notes.txt", TEXT),
        (BOM + TEXT_BYTES, "bom.txt", TEXT),
    ],
    ids=["pdf", "docx", "text", "text-with-bom"],
)
async def test_upload_stores_object_and_row(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    published: list[Any],
    data: bytes,
    filename: str,
    expected: str,
) -> None:
    owner, headers = student
    # The client's content type is ignored: the type comes from the content.
    resp = await _upload(client, headers, data, filename, "application/octet-stream")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    document_id = uuid.UUID(body["id"])
    assert document_id.version == 7
    clean_name = filename.rsplit("\\", 1)[-1]
    assert body["filename"] == clean_name
    assert body["content_type"] == expected
    assert body["size_bytes"] == len(data)
    assert set(body) == {"id", "filename", "content_type", "size_bytes", "created_at"}

    path = f"{owner}/document/{document_id}"
    assert fake_store.paths_under(f"{owner}/") == [path]
    assert fake_store.objects[path].data == data
    assert fake_store.objects[path].content_type == expected
    assert _rows(migrated_db, owner) == [(document_id, clean_name, expected, len(data))]
    # No events: Documents do not affect matching.
    assert published == []
    assert _all_jobs(migrated_db) == []


async def test_exactly_5_mib_is_accepted(client: AsyncClient, student: Student) -> None:
    _, headers = student
    data = b"a" * MAX_FILE_BYTES
    resp = await _upload(client, headers, data, "big.txt", "text/plain")
    assert resp.status_code == 201, resp.text
    assert resp.json()["size_bytes"] == MAX_FILE_BYTES


async def test_too_big_is_413_and_nothing_stored(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    data = PDF_BYTES + b"0" * (MAX_FILE_BYTES + 1 - len(PDF_BYTES))
    _assert_problem(await _upload(client, headers, data), 413, "payload-too-large", TOO_LARGE)
    assert fake_store.puts == 0 and fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


async def test_oversize_content_length_is_413_before_the_body_is_read(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    _, headers = student
    resp = await client.post(
        DOCUMENTS,
        content=b"--x--",
        headers={
            **headers,
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(MAX_FILE_BYTES + 64 * 1024 + 1),
        },
    )
    _assert_problem(resp, 413, "payload-too-large", TOO_LARGE)
    assert fake_store.puts == 0


async def test_oversize_body_without_content_length_is_413(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    _, headers = student
    chunk = b"a" * (1024 * 1024)

    async def body() -> AsyncIterator[bytes]:
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n'
        for _ in range(7):  # well past 5 MB + 64 KiB
            yield chunk

    resp = await client.post(
        DOCUMENTS,
        content=body(),
        headers={**headers, "Content-Type": "multipart/form-data; boundary=x"},
    )
    _assert_problem(resp, 413, "payload-too-large", TOO_LARGE)
    assert fake_store.puts == 0


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (b"binary \x00 with a NUL", "fake.txt", "text/plain"),
        (b"caf\xe9 au lait", "latin1.txt", "text/plain"),
        (bytes(range(1, 32)) * 8, "controls.txt", "text/plain"),
        (b"", "empty.txt", "text/plain"),
        (PNG, "photo.pdf", "application/pdf"),
        (make_docx(document=False), "notword.docx", DOCX),
    ],
    ids=["nul", "invalid-utf8", "mostly-controls", "empty", "png", "zip-without-document"],
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
    _assert_problem(resp, 415, "unsupported-media-type", WRONG_TYPE)
    assert fake_store.puts == 0
    assert _rows(migrated_db, owner) == []


async def test_missing_file_field_is_422(client: AsyncClient, student: Student) -> None:
    resp = await client.post(DOCUMENTS, data={"other": "x"}, headers=student[1])
    _assert_problem(resp, 422, "validation-error")


async def test_cap_is_409_and_nothing_stored(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_COUNT", "2")
    get_settings.cache_clear()
    try:
        owner, headers = student
        for _ in range(2):
            assert (await _upload(client, headers)).status_code == 201
        resp = await _upload(client, headers, TEXT_BYTES, "a.txt", "text/plain")
        _assert_problem(resp, 409, "conflict", "Delete an old document first")
        assert fake_store.puts == 2
        assert len(_rows(migrated_db, owner)) == 2
    finally:
        monkeypatch.delenv("DOCUMENT_MAX_COUNT")
        get_settings.cache_clear()


async def test_default_cap_is_20() -> None:
    assert get_settings().document_max_count == 20


async def test_cap_is_separate_from_resumes(
    client: AsyncClient, student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_COUNT", "1")
    get_settings.cache_clear()
    try:
        _, headers = student
        resume = await client.post(
            "/api/v1/resumes",
            files={"file": ("cv.pdf", PDF_BYTES, "application/pdf")},
            headers=headers,
        )
        assert resume.status_code == 201
        assert (await _upload(client, headers)).status_code == 201
    finally:
        monkeypatch.delenv("DOCUMENT_MAX_COUNT")
        get_settings.cache_clear()


async def test_concurrent_uploads_at_the_cap_admit_one(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_COUNT", "2")
    get_settings.cache_clear()
    try:
        owner, headers = student
        assert (await _upload(client, headers)).status_code == 201
        results = await asyncio.gather(_upload(client, headers), _upload(client, headers))
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        assert len(_rows(migrated_db, owner)) == 2
        assert len(fake_store.paths_under(f"{owner}/")) == 2
    finally:
        monkeypatch.delenv("DOCUMENT_MAX_COUNT")
        get_settings.cache_clear()


async def test_store_failure_is_500_and_no_row(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    fake_store.fail_put = True
    _assert_problem(await _upload(client, headers), 500, "internal-error")
    assert _rows(migrated_db, owner) == []
    assert fake_store.objects == {}


async def test_put_failing_after_write_still_deletes_the_object(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    owner, headers = student
    fake_store.fail_put_after_write = True
    _assert_problem(await _upload(client, headers), 500, "internal-error")
    assert fake_store.puts == 1 and fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


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

    monkeypatch.setattr(profile_repo, "insert_document", failing_insert)
    _assert_problem(await _upload(client, headers), 500, "internal-error")
    assert fake_store.puts == 1 and fake_store.objects == {}
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


async def test_cancelled_upload_deletes_the_object(
    engine: AsyncEngine,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request cancelled between the store and the insert still removes the file."""
    owner = accounts.create()
    inserting = asyncio.Event()

    async def hanging_insert(*args: Any, **kwargs: Any) -> Any:
        inserting.set()
        await asyncio.Event().wait()  # never returns

    monkeypatch.setattr(profile_repo, "insert_document", hanging_insert)
    async with AsyncSession(engine) as session:
        upload = asyncio.create_task(
            profile_service.upload_document(
                session, fake_store, owner, filename="a.txt", data=TEXT_BYTES, max_count=20
            )
        )
        await inserting.wait()
        assert fake_store.puts == 1 and fake_store.objects
        upload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await upload
        await session.rollback()
    assert fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


async def test_cancelled_commit_deletes_the_object(
    engine: AsyncEngine,
    accounts: AccountFactory,
    fake_store: FakeFileStore,
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The router's commit step: a cancelled commit still discards the stored file."""
    from app.modules.profile.router import _commit_upload

    owner = accounts.create()

    async def cancelled_commit(self: AsyncSession) -> None:
        raise asyncio.CancelledError

    async with AsyncSession(engine) as session:
        document = await profile_service.upload_document(
            session, fake_store, owner, filename="a.pdf", data=PDF_BYTES, max_count=20
        )
        assert fake_store.objects
        monkeypatch.setattr(AsyncSession, "commit", cancelled_commit)
        with pytest.raises(asyncio.CancelledError):
            await _commit_upload(
                session, lambda: profile_service.discard_document_upload(fake_store, document)
            )
        monkeypatch.undo()
        await session.rollback()
    assert fake_store.objects == {}
    assert _rows(migrated_db, owner) == []


# --------------------------------------------------------------------------- list


async def test_list_is_newest_first_with_cursor_pagination(
    client: AsyncClient, student: Student, other_student: Student
) -> None:
    _, headers = student
    ids = [(await _upload(client, headers)).json()["id"] for _ in range(3)]
    await _upload(client, other_student[1])  # never visible to `student`

    page = (await client.get(DOCUMENTS, params={"limit": 2}, headers=headers)).json()
    assert [i["id"] for i in page["items"]] == [ids[2], ids[1]]
    assert page["next_cursor"] == ids[1]

    page = (
        await client.get(
            DOCUMENTS, params={"limit": 2, "cursor": page["next_cursor"]}, headers=headers
        )
    ).json()
    assert [i["id"] for i in page["items"]] == [ids[0]]
    assert page["next_cursor"] is None

    page = (await client.get(DOCUMENTS, headers=headers)).json()
    assert [i["id"] for i in page["items"]] == ids[::-1]
    assert page["next_cursor"] is None


async def test_list_default_limit_is_20(
    client: AsyncClient, student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCUMENT_MAX_COUNT", "25")
    get_settings.cache_clear()
    try:
        _, headers = student
        for _ in range(21):
            assert (await _upload(client, headers, b"x", "x.txt", "text/plain")).status_code == 201
        page = (await client.get(DOCUMENTS, headers=headers)).json()
        assert len(page["items"]) == 20 and page["next_cursor"] is not None
    finally:
        monkeypatch.delenv("DOCUMENT_MAX_COUNT")
        get_settings.cache_clear()


@pytest.mark.parametrize("limit", [0, 51])
async def test_list_limit_bounds(client: AsyncClient, student: Student, limit: int) -> None:
    resp = await client.get(DOCUMENTS, params={"limit": limit}, headers=student[1])
    _assert_problem(resp, 422, "validation-error")


async def test_list_empty(client: AsyncClient, student: Student) -> None:
    resp = await client.get(DOCUMENTS, headers=student[1])
    assert resp.json() == {"items": [], "next_cursor": None}


# --------------------------------------------------------------------------- download


async def test_download_is_short_lived_attachment_url(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    owner, headers = student
    created = (await _upload(client, headers, TEXT_BYTES, "My Notes.txt", "text/plain")).json()
    resp = await client.get(f"{DOCUMENTS}/{created['id']}/download", headers=headers)
    after = datetime.now(UTC)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert 0 < (expires_at - after).total_seconds() <= 300
    (signed,) = fake_store.signed
    assert signed.path == f"{owner}/document/{created['id']}"
    assert signed.expires_in_seconds <= 300
    assert signed.download_name == "My Notes.txt"
    assert body["url"] == signed.url and "download=My%20Notes.txt" in body["url"]
    assert TEXT_BYTES not in resp.content


# --------------------------------------------------------------------------- delete


async def test_delete_removes_row_and_object_via_job(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    migrated_db: str,
    published: list[Any],
) -> None:
    owner, headers = student
    keep = (await _upload(client, headers)).json()
    gone = (await _upload(client, headers, TEXT_BYTES, "a.txt", "text/plain")).json()
    resp = await client.delete(f"{DOCUMENTS}/{gone['id']}", headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    assert [str(r[0]) for r in _rows(migrated_db, owner)] == [keep["id"]]
    jobs = _jobs(migrated_db)
    assert [(j[0], j[1], j[2], j[4]) for j in jobs] == [
        (
            "profile.delete_document_file",
            f"profile.delete_document_file:document:{gone['id']}",
            "succeeded",
            "priority",
        )
    ]
    assert jobs[0][3] == {"owner_id": str(owner), "document_id": gone["id"]}  # ids only
    assert fake_store.paths_under(f"{owner}/") == [f"{owner}/document/{keep['id']}"]
    assert published == []

    _assert_problem(
        await client.delete(f"{DOCUMENTS}/{gone['id']}", headers=headers), 404, "not-found"
    )


async def test_delete_job_is_idempotent_and_survives_storage_outage(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, migrated_db: str
) -> None:
    from app.core.jobs import drain
    from app.modules.profile.tasks import DELETE_RETRY, delete_document_file

    owner, headers = student
    created = (await _upload(client, headers)).json()
    fake_store.fail_delete = True
    assert (await client.delete(f"{DOCUMENTS}/{created['id']}", headers=headers)).status_code == 204
    assert _rows(migrated_db, owner) == []
    assert fake_store.paths_under(f"{owner}/")
    assert _jobs(migrated_db)[0][2] == "todo"  # failed attempt, retry scheduled

    fake_store.fail_delete = False
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("SET search_path TO app")
        conn.execute(
            "UPDATE procrastinate_jobs SET scheduled_at = now() "
            "WHERE task_name = 'profile.delete_document_file'"
        )
    await drain(queues=("priority",))
    assert fake_store.paths_under(f"{owner}/") == []
    assert _jobs(migrated_db)[0][2] == "succeeded"
    # At-least-once delivery: running it again is harmless.
    await delete_document_file(owner_id=owner, document_id=uuid.UUID(created["id"]))
    # The same long retry as Resume file deletion.
    assert DELETE_RETRY.max_attempts == 10


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
    before = _rows(migrated_db, owner)
    objects_before = dict(fake_store.objects)

    for target in (mine["id"], str(uuid.uuid7())):
        _assert_problem(
            await client.get(f"{DOCUMENTS}/{target}/download", headers=intruder), 404, "not-found"
        )
        _assert_problem(
            await client.delete(f"{DOCUMENTS}/{target}", headers=intruder), 404, "not-found"
        )

    assert _rows(migrated_db, owner) == before
    assert fake_store.objects == objects_before
    assert fake_store.signed == [] and fake_store.deleted == []
    assert _jobs(migrated_db) == []


async def test_unknown_id_is_404_for_owner(client: AsyncClient, student: Student) -> None:
    _, headers = student
    unknown = uuid.uuid7()
    _assert_problem(
        await client.get(f"{DOCUMENTS}/{unknown}/download", headers=headers), 404, "not-found"
    )
    _assert_problem(await client.delete(f"{DOCUMENTS}/{unknown}", headers=headers), 404, "not-found")


async def test_no_token_is_401_everywhere(client: AsyncClient, fake_store: FakeFileStore) -> None:
    did = uuid.uuid7()
    for method, path in [
        ("POST", DOCUMENTS),
        ("GET", DOCUMENTS),
        ("GET", f"{DOCUMENTS}/{did}/download"),
        ("DELETE", f"{DOCUMENTS}/{did}"),
    ]:
        kwargs: dict[str, Any] = {}
        if method == "POST":
            kwargs["files"] = {"file": ("a.pdf", PDF_BYTES, "application/pdf")}
        _assert_problem(await client.request(method, path, **kwargs), 401, "unauthorized")
    assert fake_store.puts == 0


async def test_account_deletion_cascades_documents(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    owner, headers = student
    await _upload(client, headers)
    await _upload(client, headers, TEXT_BYTES, "a.txt", "text/plain")
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert _rows(migrated_db, owner) == []


# --------------------------------------------------------------------------- schema


def test_documents_table_has_forced_rls_and_no_exposed_grants(
    sync_conn: psycopg.Connection,
) -> None:
    (rls, forced) = sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.documents'::regclass"
    ).fetchone()  # type: ignore[misc]
    assert rls and forced
    (grants,) = sync_conn.execute(
        "SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema = 'app' "
        "AND table_name = 'documents' AND grantee IN ('anon', 'authenticated', 'PUBLIC')"
    ).fetchone()  # type: ignore[misc]
    assert grants == 0


@pytest.mark.parametrize(
    ("content_type", "size"),
    [("image/png", 1), ("text/plain", 0), ("text/plain", MAX_FILE_BYTES + 1)],
)
def test_db_checks_type_and_size(
    sync_conn: psycopg.Connection, accounts: AccountFactory, content_type: str, size: int
) -> None:
    owner = accounts.create()
    with pytest.raises(psycopg.errors.CheckViolation):
        sync_conn.execute(
            "INSERT INTO app.documents (id, owner_id, filename, content_type, size_bytes, sha256) "
            "VALUES (%s, %s, 'a', %s, %s, %s)",
            (uuid.uuid7(), owner, content_type, size, "0" * 64),
        )


def test_bucket_allows_text_in_both_configs() -> None:
    import importlib.util
    import tomllib
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ensure_bucket", api_dir / "scripts/ensure_bucket.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tomllib.loads((api_dir.parent / "supabase/config.toml").read_text(encoding="utf-8"))
    local = config["storage"]["buckets"]["private"]["allowed_mime_types"]
    assert sorted(module.ALLOWED_MIME_TYPES) == sorted(local) == sorted([PDF, DOCX, TEXT])


async def test_stored_name_extension_matches_the_sniffed_type(
    client: AsyncClient, student: Student, fake_store: FakeFileStore
) -> None:
    _, headers = student
    html = (await _upload(client, headers, b"<p>hi</p>", "cover.html", "text/html")).json()
    assert html["content_type"] == TEXT and html["filename"] == "cover.html.txt"
    pdf = (await _upload(client, headers, PDF_BYTES, "scan.txt", "text/plain")).json()
    assert pdf["filename"] == "scan.txt.pdf"
    await client.get(f"{DOCUMENTS}/{html['id']}/download", headers=headers)
    assert fake_store.signed[-1].download_name == "cover.html.txt"
