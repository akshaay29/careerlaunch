"""FR-4 / AD-8: the Skill Profile extracted from the active Resume, the Student's edits,
reset, suggestions, and the extraction job. One test (or more) per I/O-matrix row.

Storage is the in-memory FakeFileStore. Uploads and activations run the extraction job in
the after-response ``priority`` drain, so the profile is final when the request returns.
"""

import sys
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient

from app.core import events
from app.core.config import to_libpq_url
from app.core.events import SkillProfileChanged, subscribe
from app.core.skills import VOCAB_VERSION
from app.main import create_app
from app.modules.profile import extraction
from app.modules.profile import router as profile_router
from app.modules.profile.service import UNREADABLE_MESSAGE
from app.modules.profile.tasks import extract_resume
from app.ports.file_store import FileStoreError
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.file_store import FakeFileStore
from tests.resume_fixtures import (
    MALFORMED_PDF,
    RESUME_LINES,
    make_docx,
    make_image_only_pdf,
    make_text_pdf,
)

RESUMES = "/api/v1/resumes"
PROFILE = "/api/v1/skill-profile"
PROBLEM = "application/problem+json"
EXTRACT = "profile.extract_resume"
MESSAGE = (
    "We couldn't read text from this Resume. Upload a text-based PDF or add your skills manually."
)

pytestmark = pytest.mark.usefixtures("clean_jobs")


@pytest.fixture
def fake_store() -> FakeFileStore:
    return FakeFileStore()


@pytest.fixture
def app(fake_store: FakeFileStore):
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
def changed() -> Iterator[list[SkillProfileChanged]]:
    """Record every SkillProfileChanged; the registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[SkillProfileChanged] = []

    async def record(session: Any, event: SkillProfileChanged) -> None:
        seen.append(event)

    subscribe(SkillProfileChanged, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


@pytest.fixture
def no_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routers' after-response drain does nothing: jobs stay queued for the test to run."""

    async def nothing() -> None:
        return None

    monkeypatch.setattr(profile_router, "drain_priority", nothing)


def _jobs(url: str) -> list[tuple]:
    """(queue, queueing_lock, status, attempts, args) of extraction jobs, oldest first."""
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT queue_name, queueing_lock, status, attempts, args "
            "FROM app.procrastinate_jobs WHERE task_name = %s ORDER BY id",
            (EXTRACT,),
        ).fetchall()


def _outbox(url: str) -> list[tuple]:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT task, key, queue, args FROM app.job_outbox WHERE task = %s", (EXTRACT,)
        ).fetchall()


def _row(url: str, owner_id: uuid.UUID) -> tuple | None:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT status, resume_id, extracted_skills, added_skills, removed_skills, "
            "custom_skills, vocab_version FROM app.skill_profiles WHERE owner_id = %s",
            (owner_id,),
        ).fetchone()


async def _upload(client: AsyncClient, headers: dict[str, str], data: bytes, name="cv.pdf"):
    resp = await client.post(
        RESUMES, files={"file": (name, data, "application/octet-stream")}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _profile(client: AsyncClient, headers: dict[str, str]) -> dict:
    resp = await client.get(PROFILE, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _patch(client: AsyncClient, headers: dict[str, str], **body: Any):
    return await client.patch(PROFILE, json=body, headers=headers)


def _ids(profile: dict) -> set[str]:
    return {s["id"] for s in profile["skills"]}


def _assert_422(resp) -> None:
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == "/problems/validation-error"
    assert body["errors"]


TEXT_PDF = make_text_pdf(RESUME_LINES)


# --------------------------------------------------------------------------- activation


async def test_activation_enqueues_one_priority_job_and_marks_pending(
    client: AsyncClient, student: Student, no_drain: None, migrated_db: str
) -> None:
    owner, headers = student
    resume = await _upload(client, headers, TEXT_PDF)
    outbox = _outbox(migrated_db)
    assert len(outbox) == 1
    task, key, queue, args = outbox[0]
    assert (task, key, queue) == (EXTRACT, f"{EXTRACT}:resume:{resume['id']}", "priority")
    assert args == {"owner_id": str(owner), "resume_id": resume["id"]}  # ids only

    profile = await _profile(client, headers)
    assert profile["status"] == "pending"
    assert profile["resume_id"] == resume["id"]
    assert profile["message"] is None


async def test_no_profile_is_status_none(client: AsyncClient, student: Student) -> None:
    _, headers = student
    assert await _profile(client, headers) == {
        "status": "none",
        "resume_id": None,
        "skills": [],
        "removed": [],
        "custom": [],
        "education": [],
        "experience": [],
        "vocab_version": None,
        "message": None,
        "has_edits": False,
    }


# --------------------------------------------------------------------------- extraction


@pytest.mark.parametrize(
    ("data", "name"),
    [(TEXT_PDF, "cv.pdf"), (make_docx(RESUME_LINES), "cv.docx")],
    ids=["pdf", "docx"],
)
async def test_text_resume_gives_a_ready_profile(
    client: AsyncClient,
    student: Student,
    changed: list[SkillProfileChanged],
    migrated_db: str,
    data: bytes,
    name: str,
) -> None:
    owner, headers = student
    resume = await _upload(client, headers, data, name)
    profile = await _profile(client, headers)
    assert profile["status"] == "ready"
    assert profile["resume_id"] == resume["id"]
    assert {"javascript", "react", "postgresql"} <= _ids(profile)
    assert all(s["source"] == "extracted" for s in profile["skills"])
    labels = {s["id"]: s["label"] for s in profile["skills"]}
    assert labels["javascript"] == "JavaScript" and labels["postgresql"] == "PostgreSQL"
    assert profile["education"] == [
        "B.Tech in Computer Science, Example Institute of Technology, 2022-2026"
    ]
    assert len(profile["experience"]) == 2
    assert profile["vocab_version"] == VOCAB_VERSION
    assert profile["message"] is None
    assert changed == [SkillProfileChanged(owner_id=owner)]
    assert [j[2] for j in _jobs(migrated_db)] == ["succeeded"]
    # The raw text is not stored anywhere in the row.
    row = _row(migrated_db, owner)
    assert row is not None and "Asha Verma" not in repr(row)


async def test_scanned_pdf_is_unreadable_and_manual_entry_works(
    client: AsyncClient, student: Student
) -> None:
    _, headers = student
    await _upload(client, headers, make_image_only_pdf())
    profile = await _profile(client, headers)
    assert profile["status"] == "unreadable"
    assert profile["message"] == MESSAGE == UNREADABLE_MESSAGE
    assert profile["skills"] == []

    resp = await _patch(client, headers, add=["Python"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "unreadable"
    assert body["skills"] == [{"id": "python", "label": "Python", "source": "added"}]


async def test_parser_crash_is_failed_not_retried_and_api_healthy(
    client: AsyncClient, student: Student, migrated_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    owner, headers = student
    resume = await _upload(client, headers, MALFORMED_PDF)
    profile = await _profile(client, headers)
    assert profile["status"] == "failed"
    assert profile["message"] == MESSAGE
    # The job finished (no exception), so it is not retried.
    assert [(j[2], j[3]) for j in _jobs(migrated_db)] == [("succeeded", 1)]  # one run
    assert (await client.get("/api/v1/public/health")).status_code == 200
    failures = [r for r in caplog.records if r.getMessage() == "resume_extraction_failed"]
    assert failures and failures[0].owner_id == str(owner)
    assert failures[0].resume_id == resume["id"]


async def test_time_limit_kills_child_and_api_stays_healthy(
    client: AsyncClient, student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: when the child is killed by the time limit, health still answers 200
    and later requests succeed."""
    _, headers = student
    monkeypatch.setattr(
        extraction,
        "worker_command",
        lambda kind: [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
    )
    monkeypatch.setattr(extraction, "TIMEOUT_SECONDS", 1.0)
    await _upload(client, headers, TEXT_PDF)
    profile = await _profile(client, headers)
    assert profile["status"] == "failed" and profile["message"] == MESSAGE
    assert (await client.get("/api/v1/public/health")).status_code == 200
    assert (await _patch(client, headers, add=["Go"])).status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX only")
async def test_memory_limit_is_failed_and_api_stays_healthy(
    client: AsyncClient, student: Student, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, headers = student
    monkeypatch.setattr(extraction, "MAX_MEMORY_BYTES", 16 * 1024 * 1024)
    await _upload(client, headers, TEXT_PDF)
    assert (await _profile(client, headers))["status"] == "failed"
    assert (await client.get("/api/v1/public/health")).status_code == 200


async def test_storage_error_raises_so_the_job_retries(
    client: AsyncClient, student: Student, fake_store: FakeFileStore, no_drain: None
) -> None:
    owner, headers = student
    resume = await _upload(client, headers, TEXT_PDF)
    fake_store.fail_get = True
    with pytest.raises(FileStoreError):
        await extract_resume(owner_id=owner, resume_id=uuid.UUID(resume["id"]))
    assert (await _profile(client, headers))["status"] == "pending"
    fake_store.fail_get = False
    await extract_resume(owner_id=owner, resume_id=uuid.UUID(resume["id"]))
    assert (await _profile(client, headers))["status"] == "ready"


# --------------------------------------------------------------------------- stale jobs


async def test_deleting_a_pending_resume_settles_the_profile_and_job_is_a_no_op(
    client: AsyncClient,
    student: Student,
    fake_store: FakeFileStore,
    no_drain: None,
    changed: list[SkillProfileChanged],
) -> None:
    owner, headers = student
    await _patch(client, headers, add=["Kotlin"])
    resume = await _upload(client, headers, TEXT_PDF)
    assert (await _profile(client, headers))["status"] == "pending"
    changed.clear()
    assert (await client.delete(f"{RESUMES}/{resume['id']}", headers=headers)).status_code == 204
    # The extraction will never land: no longer pending, edits kept.
    before = await _profile(client, headers)
    assert before["status"] == "none" and before["resume_id"] is None
    assert _ids(before) == {"kotlin"}
    gets = fake_store.gets
    await extract_resume(owner_id=owner, resume_id=uuid.UUID(resume["id"]))
    assert fake_store.gets == gets  # nothing downloaded
    assert await _profile(client, headers) == before
    assert changed == []


async def test_job_for_a_no_longer_active_resume_is_a_no_op(
    client: AsyncClient,
    student: Student,
    no_drain: None,
    changed: list[SkillProfileChanged],
) -> None:
    owner, headers = student
    first = await _upload(client, headers, TEXT_PDF)
    second = await _upload(client, headers, make_docx(["Python and Rust " * 10]), "b.docx")
    resp = await client.post(f"{RESUMES}/{second['id']}/activate", headers=headers)
    assert resp.status_code == 200
    await extract_resume(owner_id=owner, resume_id=uuid.UUID(first["id"]))
    profile = await _profile(client, headers)
    assert profile["status"] == "pending" and profile["resume_id"] == second["id"]
    assert changed == []

    await extract_resume(owner_id=owner, resume_id=uuid.UUID(second["id"]))
    profile = await _profile(client, headers)
    assert profile["status"] == "ready" and _ids(profile) == {"python", "rust"}
    assert changed == [SkillProfileChanged(owner_id=owner)]


async def test_job_is_idempotent(client: AsyncClient, student: Student) -> None:
    owner, headers = student
    resume = await _upload(client, headers, TEXT_PDF)
    first = await _profile(client, headers)
    await extract_resume(owner_id=owner, resume_id=uuid.UUID(resume["id"]))
    second = await _profile(client, headers)
    assert first == second


# --------------------------------------------------------------------------- edits


async def test_add_known_term(
    client: AsyncClient, student: Student, changed: list[SkillProfileChanged], migrated_db: str
) -> None:
    owner, headers = student
    resp = await _patch(client, headers, add=["JS"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "none"  # manual entry without a Resume
    assert body["skills"] == [{"id": "javascript", "label": "JavaScript", "source": "added"}]
    assert body["custom"] == []
    row = _row(migrated_db, owner)
    assert row is not None and row[0] is None and row[3] == ["javascript"]
    assert changed == [SkillProfileChanged(owner_id=owner)]


async def test_add_unknown_term_is_custom_and_not_effective(
    client: AsyncClient, student: Student, changed: list[SkillProfileChanged]
) -> None:
    owner, headers = student
    body = (await _patch(client, headers, add=["  Basket   weaving "])).json()
    assert body["custom"] == ["Basket weaving"]
    assert body["skills"] == []
    # Case-insensitive de-duplication.
    resp = await _patch(client, headers, add=["basket WEAVING"])
    assert resp.json()["custom"] == ["Basket weaving"]
    assert len(changed) == 1
    body = (await _patch(client, headers, remove_custom=["BASKET weaving"])).json()
    assert body["custom"] == []
    assert len(changed) == 2


async def test_remove_extracted_and_added(
    client: AsyncClient, student: Student, changed: list[SkillProfileChanged], migrated_db: str
) -> None:
    owner, headers = student
    await _upload(client, headers, TEXT_PDF)
    changed.clear()
    body = (await _patch(client, headers, remove=["react"])).json()
    assert "react" not in _ids(body)
    assert body["removed"] == [{"id": "react", "label": "React"}]
    assert changed == [SkillProfileChanged(owner_id=owner)]

    body = (await _patch(client, headers, add=["Kotlin"])).json()
    assert {"id": "kotlin", "label": "Kotlin", "source": "added"} in body["skills"]
    body = (await _patch(client, headers, remove=["kotlin"])).json()
    assert "kotlin" not in _ids(body)
    row = _row(migrated_db, owner)
    assert row is not None and row[3] == [] and row[4] == ["react"]

    # Adding a removed skill back takes it out of `removed`.
    body = (await _patch(client, headers, add=["react.js"])).json()
    assert "react" in _ids(body) and body["removed"] == []


async def test_edits_survive_re_extraction(client: AsyncClient, student: Student) -> None:
    _, headers = student
    await _upload(client, headers, TEXT_PDF)
    await _patch(client, headers, add=["Kotlin", "Underwater basket weaving"], remove=["react"])

    second = await _upload(
        client, headers, make_text_pdf(["Skills: React, Rust, Kotlin, SQL " * 3]), "b.pdf"
    )
    resp = await client.post(f"{RESUMES}/{second['id']}/activate", headers=headers)
    assert resp.status_code == 200
    profile = await _profile(client, headers)
    assert profile["status"] == "ready" and profile["resume_id"] == second["id"]
    assert _ids(profile) == {"rust", "kotlin", "sql"}  # react still removed
    assert profile["custom"] == ["Underwater basket weaving"]
    sources = {s["id"]: s["source"] for s in profile["skills"]}
    assert sources == {"rust": "extracted", "kotlin": "extracted", "sql": "extracted"}


async def test_reset_without_edits_changes_nothing(
    client: AsyncClient, student: Student, other_student: Student, changed: list[SkillProfileChanged]
) -> None:
    _, headers = student
    await _upload(client, headers, TEXT_PDF)
    before = await _profile(client, headers)
    assert before["has_edits"] is False
    changed.clear()
    resp = await client.post(f"{PROFILE}/reset", headers=headers)
    assert resp.status_code == 200 and resp.json() == before
    assert changed == []
    # No row at all: also a no-op.
    other_resp = await client.post(f"{PROFILE}/reset", headers=other_student[1])
    assert other_resp.status_code == 200 and other_resp.json()["status"] == "none"
    assert changed == []


async def test_has_edits_counts_an_added_skill_that_is_also_extracted(
    client: AsyncClient, student: Student
) -> None:
    _, headers = student
    await _upload(client, headers, TEXT_PDF)
    body = (await _patch(client, headers, add=["React"])).json()
    react = next(s for s in body["skills"] if s["id"] == "react")
    assert react["source"] == "extracted"
    assert body["has_edits"] is True


async def test_reset_to_extracted(
    client: AsyncClient, student: Student, changed: list[SkillProfileChanged]
) -> None:
    owner, headers = student
    await _upload(client, headers, TEXT_PDF)
    extracted = _ids(await _profile(client, headers))
    await _patch(client, headers, add=["Kotlin", "Origami"], remove=["react"])
    changed.clear()
    resp = await client.post(f"{PROFILE}/reset", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _ids(body) == extracted
    assert body["removed"] == [] and body["custom"] == []
    assert changed == [SkillProfileChanged(owner_id=owner)]


async def test_no_op_edit_is_200_without_event(
    client: AsyncClient, student: Student, changed: list[SkillProfileChanged], migrated_db: str
) -> None:
    owner, headers = student
    resp = await _patch(client, headers)
    assert resp.status_code == 200 and resp.json()["status"] == "none"
    assert _row(migrated_db, owner) is None  # nothing created for nothing

    await _patch(client, headers, add=["Python"])
    changed.clear()
    for body in ({"add": ["python"]}, {"remove": ["rust"]}, {"remove_custom": ["nothing"]}, {}):
        resp = await _patch(client, headers, **body)
        assert resp.status_code == 200, resp.text
    assert changed == []


@pytest.mark.parametrize(
    "body",
    [
        {"add": ["x" * 51]},
        {"add": ["   "]},
        {"add": ["bad\u0000term"]},
        {"remove": ["not-a-skill"]},
        {"remove": ["Not A Slug"]},
        {"add": "JS"},
        {"unexpected": []},
    ],
    ids=["too-long", "blank", "control-char", "unknown-id", "bad-slug", "not-a-list", "extra"],
)
async def test_bad_input_is_422(
    client: AsyncClient, student: Student, body: dict, migrated_db: str
) -> None:
    owner, headers = student
    _assert_422(await client.patch(PROFILE, json=body, headers=headers))
    assert _row(migrated_db, owner) is None


async def test_51_custom_skills_is_422(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    owner, headers = student
    resp = await _patch(client, headers, add=[f"Custom thing {i}" for i in range(50)])
    assert resp.status_code == 200 and len(resp.json()["custom"]) == 50
    _assert_422(await _patch(client, headers, add=["One too many"]))
    row = _row(migrated_db, owner)
    assert row is not None and len(row[5]) == 50
    # Swapping one for another stays within the cap.
    resp = await _patch(client, headers, add=["Replacement"], remove_custom=["Custom thing 0"])
    assert resp.status_code == 200 and len(resp.json()["custom"]) == 50


# --------------------------------------------------------------------------- suggestions


async def test_skill_suggestions(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.get("/api/v1/skills", params={"q": "java"}, headers=headers)
    assert resp.status_code == 200, resp.text
    found = resp.json()
    assert {"id": "java", "label": "Java"} in found
    assert {"id": "javascript", "label": "JavaScript"} in found
    assert len(found) <= 20
    resp = await client.get("/api/v1/skills", params={"q": "a"}, headers=headers)
    assert len(resp.json()) == 20
    _assert_422(await client.get("/api/v1/skills", params={"q": ""}, headers=headers))
    assert (await client.get("/api/v1/skills", params={"q": "js"})).status_code == 401


# --------------------------------------------------------------------------- isolation, auth


async def test_isolation_and_auth(
    client: AsyncClient, student: Student, other_student: Student
) -> None:
    _, headers = student
    _, other_headers = other_student
    await _patch(client, headers, add=["Python", "Origami"])
    other = await _profile(client, other_headers)
    assert other["status"] == "none" and other["skills"] == [] and other["custom"] == []
    await _patch(client, other_headers, add=["Rust"])
    assert _ids(await _profile(client, headers)) == {"python"}

    for method, url in [
        ("GET", PROFILE),
        ("PATCH", PROFILE),
        ("POST", f"{PROFILE}/reset"),
        ("GET", "/api/v1/skills?q=py"),
    ]:
        resp = await client.request(method, url, json={} if method == "PATCH" else None)
        assert resp.status_code == 401, (method, url)
        assert resp.headers["content-type"].startswith(PROBLEM)


# --------------------------------------------------------------------------- deletion


async def test_deleting_the_source_resume_keeps_the_profile(
    client: AsyncClient, student: Student
) -> None:
    _, headers = student
    resume = await _upload(client, headers, TEXT_PDF)
    before = await _profile(client, headers)
    assert (await client.delete(f"{RESUMES}/{resume['id']}", headers=headers)).status_code == 204
    after = await _profile(client, headers)
    assert after["resume_id"] is None
    assert after["status"] == "ready" and after["skills"] == before["skills"]


async def test_account_deletion_cascades_the_profile(
    client: AsyncClient, student: Student, migrated_db: str
) -> None:
    owner, headers = student
    await _patch(client, headers, add=["Python"])
    assert _row(migrated_db, owner) is not None
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert _row(migrated_db, owner) is None


def test_skill_profiles_table_has_forced_rls_and_no_exposed_grants(
    sync_conn: psycopg.Connection,
) -> None:
    rls = sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.skill_profiles'::regclass"
    ).fetchone()
    assert rls == (True, True)
    grants = sync_conn.execute(
        "SELECT grantee FROM information_schema.role_table_grants "
        "WHERE table_schema = 'app' AND table_name = 'skill_profiles' "
        "AND grantee IN ('anon', 'authenticated', 'PUBLIC')"
    ).fetchall()
    assert grants == []
