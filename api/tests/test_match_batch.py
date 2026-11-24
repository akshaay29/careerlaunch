"""FR-9, Story 3.4: ``GET /api/v1/matches?ids=``, Match Scores for one page of search results."""

import json
import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import AccountFactory, TokenFactory
from tests.listing_fixtures import draft
from tests.match_fixtures import MatchDb, ingest

pytestmark = pytest.mark.usefixtures("clean_jobs")

MATCHES = "/api/v1/matches"
LISTINGS = "/api/v1/listings"
PROBLEM = "application/problem+json"

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


def _breakdown(value: int) -> str:
    """A valid breakdown whose component points sum to ``value``."""
    skills = min(value, 70)
    return json.dumps(
        {
            "algo_version": 1,
            "weights_version": 1,
            "vocab_version": "test",
            "matched_skills": [],
            "missing_skills": [],
            "components": [
                {"name": "skills", "points": skills, "max_points": 70},
                {"name": "text", "points": value - skills, "max_points": 30},
            ],
        }
    )


def _put_score(
    db: MatchDb,
    owner: uuid.UUID,
    listing_id: uuid.UUID,
    value: int,
    breakdown: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO app.match_scores (owner_id, listing_id, score, breakdown, algo_version, "
        "weights_version, vocab_version, computed_at) "
        "VALUES (%s, %s, %s, CAST(%s AS jsonb), 1, 1, 'test', now())",
        (owner, listing_id, value, breakdown if breakdown is not None else _breakdown(value)),
    )


def _params(ids: list[uuid.UUID | str]) -> list[tuple[str, str]]:
    return [("ids", str(i)) for i in ids]


async def test_batch_mixes_stored_and_on_request_and_omits_invisible(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    other_student: Student,
) -> None:
    owner, headers = student
    other, _ = other_student
    db.set_preferences(owner, ["Backend"])
    db.set_profile(owner, ["python"])
    await ingest(engine, [draft(n) for n in range(1, 19)])  # 18 pool Listings
    await ingest(engine, [draft(19)], collected_by=other)  # private to the other Student
    ids = [db.id_of(f"job-{n}") for n in range(1, 20)]
    stored, on_request, expired, private = ids[:5], ids[5:17], ids[17], ids[18]
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (expired,))
    for n, listing_id in enumerate(stored):
        _put_score(db, owner, listing_id, 90 - n)
    # Stale stored rows of the expired and the other Student's private Listing never show.
    _put_score(db, owner, expired, 99)
    _put_score(db, owner, private, 98)
    unknown = uuid.UUID(int=404)
    asked = [*stored, *on_request, expired, private, unknown]
    assert len(asked) == 20

    resp = await client.get(MATCHES, params=_params(asked), headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 17
    assert [i["listing_id"] for i in items] == [str(i) for i in (*stored, *on_request)]
    assert [(i["score"], i["source"]) for i in items[:5]] == [
        (90, "stored"),
        (89, "stored"),
        (88, "stored"),
        (87, "stored"),
        (86, "stored"),
    ]
    assert {i["source"] for i in items[5:]} == {"on_request"}
    assert set(items[0]) == {"listing_id", "score", "source"}  # never a breakdown

    # Nothing computed on request is stored.
    assert set(db.scores(owner)) == {*stored, expired, private}

    # The one scorer: each on-request score equals the single-Listing endpoint's.
    for item in items[5:8]:
        single = await client.get(f"{LISTINGS}/{item['listing_id']}/match", headers=headers)
        assert single.json()["score"] == item["score"]
        assert single.json()["source"] == "on_request"


async def test_duplicates_give_one_item(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    _, headers = student
    await ingest(engine, [draft(1)])
    listing_id = db.id_of("job-1")
    resp = await client.get(MATCHES, params=_params([listing_id, listing_id]), headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["listing_id"] for i in items] == [str(listing_id)]
    assert items[0]["source"] == "on_request"


async def test_invalid_stored_breakdown_is_scored_on_request(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    await ingest(engine, [draft(1), draft(2)])
    broken, mismatched = db.id_of("job-1"), db.id_of("job-2")
    _put_score(db, owner, broken, 42, "{}")
    _put_score(db, owner, mismatched, 50, _breakdown(42))  # points sum to 42, not 50
    resp = await client.get(MATCHES, params=_params([broken, mismatched]), headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["source"] for i in items] == ["on_request", "on_request"]
    # The badge equals what the dialog shows.
    for item in items:
        single = await client.get(f"{LISTINGS}/{item['listing_id']}/match", headers=headers)
        assert single.json()["source"] == "on_request"
        assert single.json()["score"] == item["score"]


async def test_thirty_distinct_of_sixty_values_gives_thirty_items(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    _, headers = student
    await ingest(engine, [draft(n) for n in range(1, 31)])
    ids = [db.id_of(f"job-{n}") for n in range(1, 31)]
    resp = await client.get(MATCHES, params=_params([*ids, *ids]), headers=headers)
    assert resp.status_code == 200, resp.text
    assert [i["listing_id"] for i in resp.json()["items"]] == [str(i) for i in ids]


async def test_more_than_two_hundred_values_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    values = [uuid.UUID(int=1)] * 201
    resp = await client.get(MATCHES, params=_params(values), headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_missing_ids_is_empty(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.get(MATCHES, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


async def test_fifty_ids_is_fine_fifty_one_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    fifty = [uuid.UUID(int=n) for n in range(1, 51)]
    ok = await client.get(MATCHES, params=_params(fifty), headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"items": []}

    resp = await client.get(MATCHES, params=_params([*fifty, uuid.UUID(int=51)]), headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_malformed_id_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.get(
        MATCHES, params=_params([uuid.UUID(int=1), "not-an-id"]), headers=headers
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_signed_out_is_401(client: AsyncClient) -> None:
    resp = await client.get(MATCHES, params=_params([uuid.UUID(int=1)]))
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_openapi_documents_the_batch(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    op = schema["paths"]["/api/v1/matches"]["get"]
    assert op["operationId"] == "getMatchScores"
    assert op["tags"] == ["matching"]
    assert {"401", "422"} <= set(op["responses"])
    item = schema["components"]["schemas"]["MatchScoreItem"]
    assert set(item["properties"]) == {"listing_id", "score", "source"}
    match = schema["components"]["schemas"]["ListingMatch"]
    assert "source" in match["required"]
