"""SM-4, Story 3.4: ``POST /api/v1/listings/{id}/match/viewed``, the count-only event of
opening "Why this score"."""

import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import AccountFactory, TokenFactory
from tests.listing_fixtures import draft
from tests.match_fixtures import MatchDb, ingest

pytestmark = pytest.mark.usefixtures("clean_jobs")

LISTINGS = "/api/v1/listings"
PROBLEM = "application/problem+json"
KIND = "matching.breakdown_opened"

Student = tuple[uuid.UUID, dict[str, str]]


def _student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    owner = accounts.create()
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    return owner, tokens.headers(tokens.token(owner, email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def other_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def db(migrated_db: str) -> Iterator[MatchDb]:
    match_db = MatchDb(migrated_db)
    match_db.clear()
    yield match_db
    match_db.clear()


def _events(db: MatchDb, owner: uuid.UUID) -> list[tuple]:
    return db.execute(
        "SELECT owner_id, entity_type, entity_id, counts FROM app.ops_events "
        "WHERE kind = %s AND owner_id = %s",
        (KIND, owner),
    )


async def test_opening_records_one_count_only_event(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    await ingest(engine, [draft(1)])
    listing_id = db.id_of("job-1")
    resp = await client.post(f"{LISTINGS}/{listing_id}/match/viewed", headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    # owner_id and a count only: no Listing, score or skill.
    assert _events(db, owner) == [(owner, None, None, {"opened": 1})]

    again = await client.post(f"{LISTINGS}/{listing_id}/match/viewed", headers=headers)
    assert again.status_code == 204
    assert len(_events(db, owner)) == 2


async def test_invisible_listing_is_404_and_records_nothing(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    other_student: Student,
) -> None:
    owner, headers = student
    other, _ = other_student
    await ingest(engine, [draft(1)], collected_by=other)  # private to the other Student
    await ingest(engine, [draft(2)])
    private, expired = db.id_of("job-1"), db.id_of("job-2")
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (expired,))
    for listing_id in (private, expired, uuid.UUID(int=404)):
        resp = await client.post(f"{LISTINGS}/{listing_id}/match/viewed", headers=headers)
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith(PROBLEM)
    assert _events(db, owner) == []


async def test_signed_out_is_401(client: AsyncClient) -> None:
    resp = await client.post(f"{LISTINGS}/{uuid.UUID(int=1)}/match/viewed")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_openapi_documents_the_event(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    op = schema["paths"]["/api/v1/listings/{listing_id}/match/viewed"]["post"]
    assert op["operationId"] == "recordBreakdownViewed"
    assert op["tags"] == ["matching"]
    assert {"204", "401", "404"} <= set(op["responses"])
