"""FR-9, AD-9: ``GET /api/v1/listings/{id}/match``, the on-request Match Score (Story 3.1)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import skills
from app.modules.listings import service as listings_service
from app.modules.matching import service as matching_service
from app.modules.profile import service as profile_service
from app.ports.source_adapter import SourceQuery
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.source_adapter import FakeSourceAdapter
from tests.listing_fixtures import ListingDb, draft, listing_db  # noqa: F401 (fixture)

LISTINGS = "/api/v1/listings"
PROBLEM = "application/problem+json"

pytestmark = pytest.mark.usefixtures("clean_jobs")

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


async def _collect(engine: AsyncEngine, drafts, *, collected_by=None) -> None:
    async with AsyncSession(engine) as session:
        await listings_service.collect_and_ingest(
            session,
            FakeSourceAdapter(drafts=drafts),
            SourceQuery(limit=100),
            collected_by=collected_by,
        )
        await session.commit()


def _id_of(db: ListingDb, source_job_id: str) -> uuid.UUID:
    return db.execute("SELECT id FROM app.listings WHERE source_job_id = %s", (source_job_id,))[
        0
    ][0]


def _set_profile(
    db: ListingDb,
    owner_id: uuid.UUID,
    *,
    extracted: list[str],
    added: list[str] = (),  # type: ignore[assignment]
    removed: list[str] = (),  # type: ignore[assignment]
    custom: list[str] = (),  # type: ignore[assignment]
    vocab: str = skills.VOCAB_VERSION,
) -> None:
    db.execute(
        "INSERT INTO app.skill_profiles (owner_id, status, extracted_skills, added_skills, "
        "removed_skills, custom_skills, vocab_version) VALUES (%s, 'ready', %s, %s, %s, %s, %s)",
        (owner_id, extracted, list(added), list(removed), list(custom), vocab),
    )


def _set_roles(db: ListingDb, owner_id: uuid.UUID, roles: list[str]) -> None:
    db.execute(
        "INSERT INTO app.preferences (owner_id, role_titles) VALUES (%s, %s)", (owner_id, roles)
    )


def _match(item: dict) -> tuple[int, dict]:
    return item["score"], item["breakdown"]


@pytest.mark.parametrize("path", [f"{LISTINGS}/{uuid.uuid4()}/match"])
async def test_no_token_is_401(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_scores_a_visible_listing_with_breakdown(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    listing_db: ListingDb,
) -> None:
    owner_id, headers = student
    # The fixture description mentions Python, PostgreSQL and Docker.
    await _collect(engine, [draft(1, title="Backend Engineering Intern")])
    listing_id = _id_of(listing_db, "job-1")
    # "postgres" is an alias id that the current vocabulary maps to "postgresql"; the
    # removed and custom skills are never scored.
    _set_profile(
        listing_db,
        owner_id,
        extracted=["python", "postgres", "docker"],
        removed=["docker"],
        custom=["Docker Swarm"],
        vocab="2020.01.0",
    )
    _set_roles(listing_db, owner_id, ["Backend Engineer"])

    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"listing_id", "score", "breakdown"}
    assert body["listing_id"] == str(listing_id)
    value, breakdown = _match(body)
    assert 0 <= value <= 100
    assert sum(c["points"] for c in breakdown["components"]) == value
    assert [c["name"] for c in breakdown["components"]] == ["skills", "text"]
    assert breakdown["matched_skills"] == [
        {"id": "postgresql", "label": "PostgreSQL"},
        {"id": "python", "label": "Python"},
    ]
    assert breakdown["missing_skills"] == [{"id": "docker", "label": "Docker"}]
    skills_component = breakdown["components"][0]
    assert skills_component == {"name": "skills", "points": 47, "max_points": 70}
    # "backend", "engineer" (stem "engin") and "python" are lexemes of the search_doc.
    assert breakdown["components"][1]["points"] > 15
    assert breakdown["algo_version"] == 1
    assert breakdown["weights_version"] == 1
    assert breakdown["vocab_version"] == skills.VOCAB_VERSION

    # Read-only and deterministic: the same request gives the same body.
    again = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert again.json() == body


async def test_student_without_profile_or_preferences_gets_a_valid_score(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    listing_id = _id_of(listing_db, "job-1")
    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert resp.status_code == 200, resp.text
    value, breakdown = _match(resp.json())
    assert value == 0
    assert breakdown["matched_skills"] == []
    assert [s["id"] for s in breakdown["missing_skills"]] == ["docker", "postgresql", "python"]
    assert breakdown["components"] == [
        {"name": "skills", "points": 0, "max_points": 70},
        {"name": "text", "points": 0, "max_points": 30},
    ]


async def test_listing_without_skills_puts_all_weight_on_text(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    owner_id, headers = student
    await _collect(
        engine,
        [draft(1, title="Data Analyst", description="Analyse numbers and write reports.")],
    )
    listing_id = _id_of(listing_db, "job-1")
    _set_roles(listing_db, owner_id, ["Data Analyst"])
    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert resp.status_code == 200, resp.text
    value, breakdown = _match(resp.json())
    assert breakdown["components"][0] == {"name": "skills", "points": 0, "max_points": 0}
    assert breakdown["components"][1]["max_points"] == 100
    assert value == 100  # identical title, both words are lexemes


async def test_other_students_private_listing_is_404(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    other_student: Student,
    listing_db: ListingDb,
) -> None:
    owner_id, owner_headers = student
    _, other_headers = other_student
    await _collect(engine, [draft(1)], collected_by=owner_id)  # no pool consent: private
    listing_id = _id_of(listing_db, "job-1")
    assert (
        await client.get(f"{LISTINGS}/{listing_id}/match", headers=owner_headers)
    ).status_code == 200
    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=other_headers)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/not-found"


async def test_expired_and_unknown_listings_are_404(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    listing_id = _id_of(listing_db, "job-1")
    listing_db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (listing_id,))
    assert (
        await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    ).status_code == 404
    assert (
        await client.get(f"{LISTINGS}/{uuid.uuid4()}/match", headers=headers)
    ).status_code == 404


async def test_openapi_exposes_the_breakdown(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    op = schema["paths"]["/api/v1/listings/{listing_id}/match"]["get"]
    assert op["operationId"] == "getListingMatch"
    assert op["tags"] == ["matching"]
    breakdown = schema["components"]["schemas"]["ScoreBreakdown"]
    assert set(breakdown["properties"]) == {
        "algo_version",
        "weights_version",
        "vocab_version",
        "matched_skills",
        "missing_skills",
        "components",
    }


async def test_taken_down_listing_is_404(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    listing_id = _id_of(listing_db, "job-1")
    assert (await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)).status_code == 200
    async with AsyncSession(engine) as session:
        assert await listings_service.block(session, "listing", "linkedin:job-1") == 1
        await session.commit()
    try:
        resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith(PROBLEM)
    finally:
        # The block outlives the Listing; lift it so later tests can ingest job-1 again.
        async with AsyncSession(engine) as session:
            await listings_service.unblock(session, "listing", "linkedin:job-1")
            await session.commit()


async def test_listing_without_lexemes_scores_text_from_title_only(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    owner_id, headers = student
    await _collect(engine, [draft(1, title="Backend Intern")])
    listing_id = _id_of(listing_db, "job-1")
    listing_db.execute(
        "UPDATE app.listings SET search_doc = ''::tsvector WHERE id = %s", (listing_id,)
    )
    _set_roles(listing_db, owner_id, ["Backend Intern"])
    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert resp.status_code == 200, resp.text
    value, breakdown = _match(resp.json())
    # Title similarity 1.0, lexeme coverage 0: half of the 30 text points.
    assert breakdown["components"] == [
        {"name": "skills", "points": 0, "max_points": 70},
        {"name": "text", "points": 15, "max_points": 30},
    ]
    assert value == 15


async def test_malformed_listing_id_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.get(f"{LISTINGS}/not-an-id/match", headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_null_array_elements_are_ignored(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    owner_id, headers = student
    await _collect(engine, [draft(1, title="Backend Intern")])
    listing_id = _id_of(listing_db, "job-1")
    listing_db.execute(
        "UPDATE app.listings SET skills = array_append(array_append(skills, NULL), '') "
        "WHERE id = %s",
        (listing_id,),
    )
    listing_db.execute(
        "INSERT INTO app.skill_profiles (owner_id, status, extracted_skills, added_skills, "
        "vocab_version) VALUES (%s, 'ready', ARRAY['python', NULL, ''], ARRAY[NULL]::text[], %s)",
        (owner_id, skills.VOCAB_VERSION),
    )
    # Preferences reject NULL or blank role titles in the DB; see the unit test below.
    _set_roles(listing_db, owner_id, ["Backend Intern"])
    resp = await client.get(f"{LISTINGS}/{listing_id}/match", headers=headers)
    assert resp.status_code == 200, resp.text
    value, breakdown = _match(resp.json())
    assert [s["id"] for s in breakdown["matched_skills"]] == ["python"]
    assert [s["id"] for s in breakdown["missing_skills"]] == ["docker", "postgresql"]
    assert sum(c["points"] for c in breakdown["components"]) == value


async def test_scoring_profile_drops_null_and_blank_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_profile = SimpleNamespace(
        extracted_skills=["python", None, ""],
        added_skills=[None],
        removed_skills=[],
        vocab_version="2026.11.1",
    )
    preferences = SimpleNamespace(role_titles=[None, "", "  ", "Backend Intern"])

    async def fake_skill_profile(_session, _owner_id):
        return skill_profile

    async def fake_preferences(_session, _owner_id):
        return preferences

    monkeypatch.setattr(profile_service, "get_skill_profile", fake_skill_profile)
    monkeypatch.setattr(profile_service, "get_preferences", fake_preferences)
    session: Any = None  # never used: both loaders are replaced
    profile = await matching_service.scoring_profile(session, uuid.UUID(int=1))
    assert profile.skills == frozenset({"python"})
    assert profile.role_titles == ("Backend Intern",)
    assert profile.vocab_version == "2026.11.1"
