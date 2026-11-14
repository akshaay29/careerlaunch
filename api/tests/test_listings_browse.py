"""FR-6, FR-7, AD-5, AD-7: browsing the shared Listing pool (Story 2.1). One test (or more)
per browse row of the I/O matrix."""

import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import skills
from app.modules.listings import service
from app.ports.source_adapter import SourceQuery
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.source_adapter import FakeSourceAdapter
from tests.listing_fixtures import ListingDb, draft, listing_db  # noqa: F401 (fixture)

LISTINGS = "/api/v1/listings"
PROBLEM = "application/problem+json"

pytestmark = pytest.mark.usefixtures("clean_jobs")

Student = tuple[uuid.UUID, dict[str, str]]

ITEM_FIELDS = {
    "id",
    "title",
    "company",
    "location",
    "job_type",
    "posted_at",
    "deadline",
    "url",
    "source",
    "source_label",
    "skills",
    "first_seen_at",
}


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


async def _collect(engine: AsyncEngine, drafts, *, collected_by=None) -> int:
    async with AsyncSession(engine) as session:
        count = await service.collect_and_ingest(
            session,
            FakeSourceAdapter(drafts=drafts),
            SourceQuery(limit=1000),
            collected_by=collected_by,
        )
        await session.commit()
    return count


async def _all_pages(client: AsyncClient, headers: dict[str, str], limit: int) -> list[dict]:
    items: list[dict] = []
    cursor: str | None = None
    for _ in range(100):
        params: dict[str, object] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = await client.get(LISTINGS, params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) <= limit
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("pagination did not end")


# --------------------------------------------------------------------------- auth


@pytest.mark.parametrize("path", [LISTINGS, f"{LISTINGS}/{uuid.uuid4()}"])
async def test_no_token_is_401(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/unauthorized"


# --------------------------------------------------------------------------- end to end


async def test_end_to_end_twenty_drafts_newest_first_and_paged(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    assert await _collect(engine, [draft(n) for n in range(1, 21)]) == 20
    items = await _all_pages(client, headers, limit=7)
    assert len(items) == 20
    assert len({i["id"] for i in items}) == 20
    keys = [(i["first_seen_at"], i["id"]) for i in items]
    stamps = [(datetime.fromisoformat(s), uuid.UUID(i)) for s, i in keys]
    assert stamps == sorted(stamps, reverse=True)
    # Ingested in order 1..20, so the newest (job-20) comes first.
    assert items[0]["title"] == "Backend Intern 20"
    assert items[-1]["title"] == "Backend Intern 1"
    # One page of everything gives the same order.
    resp = await client.get(LISTINGS, params={"limit": 50}, headers=headers)
    assert [i["id"] for i in resp.json()["items"]] == [i["id"] for i in items]
    assert resp.json()["next_cursor"] is None


async def test_item_shape(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(
        engine,
        [
            draft(
                1,
                posted_at=datetime(2026, 11, 1, 9, 30, tzinfo=UTC),
                deadline=date(2026, 12, 15),
            )
        ],
    )
    resp = await client.get(LISTINGS, headers=headers)
    assert resp.status_code == 200
    (item,) = resp.json()["items"]
    assert set(item) == ITEM_FIELDS
    assert item["title"] == "Backend Intern 1"
    assert item["company"] == "Acme Corp" and item["location"] == "Bengaluru"
    assert item["job_type"] == "internship"
    assert item["posted_at"].startswith("2026-11-01T09:30:00")
    assert item["deadline"] == "2026-12-15"
    assert item["url"] == "https://www.linkedin.com/jobs/view/1"
    assert (item["source"], item["source_label"]) == ("linkedin", "LinkedIn")
    assert {"id": "python", "label": "Python"} in item["skills"]
    assert all(s["label"] == skills.label(s["id"]) for s in item["skills"])
    detail = await client.get(f"{LISTINGS}/{item['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json() == item


async def test_default_and_max_limit(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(n) for n in range(1, 26)])
    resp = await client.get(LISTINGS, headers=headers)
    assert len(resp.json()["items"]) == 20 and resp.json()["next_cursor"]
    for bad in (0, 51):
        resp = await client.get(LISTINGS, params={"limit": bad}, headers=headers)
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith(PROBLEM)


@pytest.mark.parametrize("cursor", ["not-a-cursor", "%%%", "YWJj", "x" * 201])
async def test_invalid_cursor_is_422(
    client: AsyncClient, student: Student, listing_db: ListingDb, cursor: str
) -> None:
    _, headers = student
    resp = await client.get(LISTINGS, params={"cursor": cursor}, headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/validation-error"


async def test_empty_pool(client: AsyncClient, student: Student, listing_db: ListingDb) -> None:
    _, headers = student
    resp = await client.get(LISTINGS, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_cursor": None}


# --------------------------------------------------------------------------- visibility


async def test_private_listing_visible_only_to_collector(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    other_student: Student,
    listing_db: ListingDb,
) -> None:
    owner_id, owner_headers = student
    _, other_headers = other_student
    await _collect(engine, [draft(1)], collected_by=owner_id)  # no pool consent: private
    await _collect(engine, [draft(2)])  # System: pool

    own = (await client.get(LISTINGS, headers=owner_headers)).json()["items"]
    assert {i["title"] for i in own} == {"Backend Intern 1", "Backend Intern 2"}
    private_id = next(i["id"] for i in own if i["title"] == "Backend Intern 1")
    assert (await client.get(f"{LISTINGS}/{private_id}", headers=owner_headers)).status_code == 200

    others = (await client.get(LISTINGS, headers=other_headers)).json()["items"]
    assert [i["title"] for i in others] == ["Backend Intern 2"]
    resp = await client.get(f"{LISTINGS}/{private_id}", headers=other_headers)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/not-found"


async def test_widened_listing_visible_to_everyone(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    other_student: Student,
    accounts: AccountFactory,
    listing_db: ListingDb,
) -> None:
    owner_id, _ = student
    _, other_headers = other_student
    consenting = accounts.create()
    listing_db.grant_pool(consenting)
    await _collect(engine, [draft(1)], collected_by=owner_id)
    assert (await client.get(LISTINGS, headers=other_headers)).json()["items"] == []
    await _collect(engine, [draft(1)], collected_by=consenting)
    items = (await client.get(LISTINGS, headers=other_headers)).json()["items"]
    assert [i["title"] for i in items] == ["Backend Intern 1"]


async def test_unknown_id_is_404(
    client: AsyncClient, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    resp = await client.get(f"{LISTINGS}/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_expired_listing_hidden(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1), draft(2)])
    (expired_id,) = listing_db.execute(
        "UPDATE app.listings SET expired_at = now() WHERE source_job_id = 'job-1' RETURNING id"
    )[0]
    items = (await client.get(LISTINGS, headers=headers)).json()["items"]
    assert [i["title"] for i in items] == ["Backend Intern 2"]
    assert (await client.get(f"{LISTINGS}/{expired_id}", headers=headers)).status_code == 404


async def test_disabled_source_still_browses(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    listing_db.execute("UPDATE app.sources SET enabled = false WHERE id = 'linkedin'")
    assert await _collect(engine, [draft(2)]) == 0
    items = (await client.get(LISTINGS, headers=headers)).json()["items"]
    assert [i["title"] for i in items] == ["Backend Intern 1"]


async def test_scraped_text_is_returned_verbatim(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    """The API never interprets scraped text; the web renders it as plain text."""
    _, headers = student
    title = "<script>alert('x')</script> & Intern"
    await _collect(engine, [draft(1, title=title, company_raw="<b>Evil</b>")])
    (item,) = (await client.get(LISTINGS, headers=headers)).json()["items"]
    assert item["title"] == title and item["company"] == "<b>Evil</b>"


async def test_keyset_ties_page_by_id(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(n) for n in range(1, 6)])
    listing_db.execute("UPDATE app.listings SET first_seen_at = '2026-11-14T00:00:00Z'")
    items = await _all_pages(client, headers, limit=2)
    ids = [i["id"] for i in items]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert ids == sorted(ids, key=uuid.UUID, reverse=True)


async def test_exact_multiple_pages_end_without_an_empty_page(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(n) for n in range(1, 21)])
    first = (await client.get(LISTINGS, params={"limit": 10}, headers=headers)).json()
    assert len(first["items"]) == 10 and first["next_cursor"]
    second = (
        await client.get(
            LISTINGS, params={"limit": 10, "cursor": first["next_cursor"]}, headers=headers
        )
    ).json()
    assert len(second["items"]) == 10
    assert second["next_cursor"] is None


async def test_unknown_skill_id_falls_back_to_id_label(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    listing_db.execute("UPDATE app.listings SET skills = ARRAY['retired-skill', 'python']")
    (item,) = (await client.get(LISTINGS, headers=headers)).json()["items"]
    assert item["skills"] == [
        {"id": "retired-skill", "label": "retired-skill"},
        {"id": "python", "label": "Python"},
    ]
