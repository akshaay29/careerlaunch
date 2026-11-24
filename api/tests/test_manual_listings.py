"""FR-18, AD-7, AD-5: adding a job from another site as a private manual Listing, saved and
scored like any other (Story 4.3). One test (or more) per row of the I/O matrix."""

import logging
import socket
import urllib.request
import uuid
from collections.abc import Iterator

import httpx
import psycopg
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.listings import service as listings_service
from app.modules.tracker import router as tracker_router
from app.ports.source_adapter import ListingDraft
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)
from tests.match_fixtures import MatchDb, recompute
from tests.tracker_fixtures import (  # noqa: F401 (fixtures)
    APPLICATIONS,
    PROBLEM,
    Student,
    TrackerDb,
    other_student,
    save,
    student,
)

pytestmark = pytest.mark.usefixtures("clean_jobs")

MANUAL = f"{APPLICATIONS}/manual"
LISTINGS = "/api/v1/listings"
# A word only in the description of FULL (never in the title or company).
SECRET_WORD = "quokkaplatformzeta"
FULL = {
    "url": "https://jobs.example.com/123",
    "title": "Platform Intern",
    "company": "Example Labs",
    "location": "Pune",
    "job_type": "internship",
    "deadline": "2030-06-30",
    "description": f"You will write Python services and ship them with Docker. {SECRET_WORD}.",
}


class ManualDb(TrackerDb, MatchDb):
    def manual_rows(self, owner_id: uuid.UUID) -> list[tuple]:
        return self.execute(
            "SELECT id, url, title, visibility_owner_id, collected_by, private_description "
            "FROM app.listings WHERE source = 'manual' AND visibility_owner_id = %s",
            (owner_id,),
        )

    def manual_count(self) -> int:
        return self.execute("SELECT count(*) FROM app.listings WHERE source = 'manual'")[0][0]


@pytest.fixture
def db(mdb: MaintenanceDb, migrated_db: str) -> ManualDb:  # noqa: F811
    """Listings and takedowns start and end empty (``mdb``); Applications go with accounts."""
    return ManualDb(migrated_db)


@pytest.fixture
def drains(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the after-response drain with a counter."""
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    monkeypatch.setattr(tracker_router, "drain_priority", counted)
    return calls


async def _add(client: AsyncClient, headers: dict[str, str], body: dict) -> httpx.Response:
    return await client.post(MANUAL, json=body, headers=headers)


def _error_loc(resp: httpx.Response) -> list[str]:
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    return resp.json()["errors"][0]["loc"]


# --------------------------------------------------------------------------- full form


async def test_full_form_creates_a_private_saved_pinned_application(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    resp = await _add(client, headers, FULL)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "saved" and body["allowed_next"] == ["applied", "withdrawn"]
    assert body["is_manual"] is True
    assert body["description"] == FULL["description"]
    assert body["title"] == "Platform Intern" and body["company"] == "Example Labs"
    assert body["url"] == FULL["url"] and body["deadline"] == "2030-06-30"
    assert body["listing_available"] is True

    ((listing_id, url, title, visible_to, collector, private),) = db.manual_rows(owner)
    assert str(listing_id) == body["listing_id"]
    assert (url, title, visible_to, collector) == (FULL["url"], "Platform Intern", owner, owner)
    assert private == FULL["description"]
    row = db.listing(listing_id)
    assert row is not None
    assert {"python", "docker"} <= set(row["skills"])
    assert row["location"] == "Pune" and row["job_type"] == "internship"
    assert SECRET_WORD[:8] in row["search_doc_text"]  # description lexemes are searchable
    assert db.pin_count(listing_id) == 1
    assert db.history_rows(body["id"]) == [(None, "saved")]

    # The Student's scores are marked stale on the priority queue, then drained after the
    # response.
    keys = [(key, queue) for key, queue, _ in db.outbox()]
    assert (f"matching.recompute:student:{owner}:priority", "priority") in keys
    assert db.state(owner)[0] is not None  # type: ignore[index]
    assert {"manual_listing": 1} in db.ops("matching.marked_stale")
    assert drains == [1]


async def test_owner_sees_it_as_added_by_them(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    _, headers = student
    body = (await _add(client, headers, FULL)).json()
    detail = await client.get(f"{LISTINGS}/{body['listing_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["source"] == "manual" and detail.json()["url"] == FULL["url"]
    assert "description" not in detail.json()
    again = await client.get(f"{APPLICATIONS}/{body['id']}", headers=headers)
    assert again.json()["is_manual"] is True
    assert again.json()["description"] == FULL["description"]
    # The owner can search it, including by a word from the description.
    found = await client.get(f"{LISTINGS}/search", params={"q": SECRET_WORD}, headers=headers)
    assert [i["id"] for i in found.json()["items"]] == [body["listing_id"]]
    # Tracker rows say it is manual but leave the description to the Application.
    page = (await client.get(APPLICATIONS, headers=headers)).json()
    assert page["items"][0]["is_manual"] is True
    assert page["items"][0]["description"] is None


async def test_scored_after_the_drain(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    db.set_profile(owner, ["python"])
    db.set_preferences(owner, ["Data Analyst"])  # the manual title does not match: still scored
    body = (await _add(client, headers, FULL)).json()
    await recompute(owner, priority=True)
    listing_id = uuid.UUID(body["listing_id"])
    assert listing_id in db.scores(owner)
    page = (await client.get(APPLICATIONS, headers=headers)).json()
    assert page["items"][0]["score"] == db.scores(owner)[listing_id]


# --------------------------------------------------------------------------- partial forms


async def test_url_only_derives_the_title(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    _, headers = student
    resp = await _add(client, headers, {"url": "https://Jobs.Example.com/123"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "Job at jobs.example.com"
    assert resp.json()["description"] is None and resp.json()["company"] == ""


@pytest.mark.parametrize(
    ("url", "title"),
    [
        ("https://Bücher.Example/jobs/1", "Job at bücher.example"),  # IDN kept as typed
        ("https://x.com:99999/job", "Job at x.com"),
        ("HTTPS://user@Jobs.Example.com:8443/a?b#c", "Job at jobs.example.com"),
        ("https://[::1]:8080/x", "Job at [::1]"),
    ],
)
async def test_derived_title_uses_the_raw_host(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int], url: str, title: str
) -> None:
    resp = await _add(client, student[1], {"url": url})
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == title


async def test_title_only_has_no_url(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    resp = await _add(client, headers, {"title": "  Data Intern  ", "url": "  "})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Data Intern" and body["url"] is None
    ((_, url, _, _, _, private),) = db.manual_rows(owner)
    assert url is None and private is None
    assert db.application(body["id"])["url"] is None  # type: ignore[index]
    detail = await client.get(f"{LISTINGS}/{body['listing_id']}", headers=headers)
    assert detail.json()["url"] is None


async def test_title_only_twice_makes_two_listings(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    a = await _add(client, headers, {"title": "Data Intern"})
    b = await _add(client, headers, {"title": "Data Intern"})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert len(db.manual_rows(owner)) == 2


# --------------------------------------------------------------------------- invalid input


@pytest.mark.parametrize("body", [{}, {"title": "   "}, {"title": None, "url": None}])
async def test_neither_title_nor_url_is_422_on_title(
    client: AsyncClient, student: Student, db: ManualDb, body: dict
) -> None:
    resp = await _add(client, student[1], body)
    assert _error_loc(resp) == ["body", "title"]
    assert db.manual_count() == 0


@pytest.mark.parametrize(
    "url",
    ["ftp://x", "javascript:alert(1)", "https://", "jobs.example.com/1", "https://a b.com/x"],
)
async def test_bad_url_is_422_on_url(
    client: AsyncClient, student: Student, db: ManualDb, url: str
) -> None:
    for body in ({"url": url}, {"url": url, "title": "Data Intern"}):
        resp = await _add(client, student[1], body)
        assert _error_loc(resp) == ["body", "url"]
    assert db.manual_count() == 0


async def test_description_limit_counts_code_points(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    _, headers = student
    too_long = await _add(client, headers, {"title": "Data Intern", "description": "😀" * 4001})
    assert _error_loc(too_long) == ["body", "description"]
    assert db.manual_count() == 0
    fits = await _add(client, headers, {"title": "Data Intern", "description": "😀" * 4000})
    assert fits.status_code == 201, fits.text


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", "x" * 301), ("company", "x" * 201), ("location", "x" * 201)],
)
async def test_over_long_text_is_422_on_its_field(
    client: AsyncClient, student: Student, db: ManualDb, field: str, value: str
) -> None:
    body = {"title": "Data Intern", field: value}
    assert _error_loc(await _add(client, student[1], body)) == ["body", field]


async def test_old_deadline_is_422(client: AsyncClient, student: Student, db: ManualDb) -> None:
    resp = await _add(client, student[1], {"title": "Data Intern", "deadline": "1999-12-31"})
    assert _error_loc(resp) == ["body", "deadline"]
    assert db.manual_count() == 0


async def test_unknown_job_type_is_422(
    client: AsyncClient, student: Student, db: ManualDb
) -> None:
    resp = await _add(client, student[1], {"title": "Data Intern", "job_type": "contract"})
    assert _error_loc(resp) == ["body", "job_type"]


async def test_nul_in_title_is_422(client: AsyncClient, student: Student, db: ManualDb) -> None:
    resp = await _add(client, student[1], {"title": "Data\x00Intern"})
    assert _error_loc(resp) == ["body", "title"]


# --------------------------------------------------------------------------- duplicates


async def test_same_canonical_url_returns_the_existing_application(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    first = await _add(client, headers, FULL)
    db.clear_outbox()
    again = await _add(
        client,
        headers,
        {"url": "HTTPS://jobs.EXAMPLE.com:443/123/#top", "title": "Something else"},
    )
    assert first.status_code == 201 and again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["title"] == "Platform Intern"  # nothing changed
    assert len(db.manual_rows(owner)) == 1
    assert db.pin_count(uuid.UUID(first.json()["listing_id"])) == 1
    assert db.outbox() == [] and drains == [1]


async def test_query_string_tells_jobs_apart(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    board = "https://boards.example.com/acme/jobs"
    first = await _add(client, headers, {"url": f"{board}?gh_jid=1"})
    second = await _add(client, headers, {"url": f"{board}?gh_jid=2"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    again = await _add(client, headers, {"url": f"HTTPS://Boards.Example.com:443/acme/jobs/?gh_jid=1#x"})
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert len(db.manual_rows(owner)) == 2


async def test_readding_a_link_whose_listing_expired_returns_its_application(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    first = (await _add(client, headers, FULL)).json()
    db.execute(
        "UPDATE app.listings SET expired_at = now() WHERE id = %s", (first["listing_id"],)
    )
    again = await _add(client, headers, FULL)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first["id"]
    assert again.json()["listing_available"] is False and again.json()["is_manual"] is True
    assert len(db.manual_rows(owner)) == 1


async def test_readding_a_link_whose_listing_has_no_application_adds_a_new_one(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    first = (await _add(client, headers, FULL)).json()
    # An orphan: the Application is gone but its manual Listing is not.
    db.execute("DELETE FROM app.applications WHERE id = %s", (first["id"],))
    again = await _add(client, headers, FULL)
    assert again.status_code == 201, again.text
    assert again.json()["listing_id"] != first["listing_id"]
    assert len(db.manual_rows(owner)) == 2


async def test_readding_a_link_taken_down_since_is_422(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    db: ManualDb,
    drains: list[int],
) -> None:
    owner, headers = student
    first = (await _add(client, headers, FULL)).json()
    async with AsyncSession(engine) as session:
        await listings_service.block(session, "url", FULL["url"])
        await session.commit()
    again = await _add(client, headers, FULL)
    assert _error_loc(again) == ["body", "url"]
    assert db.exists(uuid.UUID(first["listing_id"]))  # the sweep spares manual Listings


async def test_another_student_gets_their_own_listing(
    client: AsyncClient, student: Student, other_student: Student, db: ManualDb, drains: list[int]
) -> None:
    a = (await _add(client, student[1], FULL)).json()
    resp = await _add(client, other_student[1], FULL)
    assert resp.status_code == 201
    b = resp.json()
    assert a["listing_id"] != b["listing_id"]
    assert len(db.manual_rows(student[0])) == len(db.manual_rows(other_student[0])) == 1


async def test_a_pool_listing_with_the_same_url_is_not_reused(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    pool = db.add("job-1", url=FULL["url"])
    body = (await _add(client, student[1], FULL)).json()
    assert body["listing_id"] != str(pool)
    assert db.manual_count() == 1


# --------------------------------------------------------------------------- takedown


@pytest.mark.parametrize(
    ("kind", "value", "body"),
    [
        ("url", "https://jobs.example.com/123/", {"url": "https://JOBS.example.com/123?x=1"}),
        ("company", "Example Labs", {"title": "Data Intern", "company": " example labs "}),
    ],
)
async def test_blocked_job_is_422_and_nothing_is_written(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    db: ManualDb,
    drains: list[int],
    kind: str,
    value: str,
    body: dict,
) -> None:
    owner, headers = student
    async with AsyncSession(engine) as session:
        await listings_service.block(session, kind, value)
        await session.commit()
    resp = await _add(client, headers, body)
    assert _error_loc(resp) == ["body", "url" if kind == "url" else "company"]
    assert resp.json()["errors"][0]["msg"] == "This job can't be added."
    assert db.manual_count() == 0
    assert db.count_applications(owner) == 0
    assert db.outbox() == [] and drains == []


@pytest.mark.parametrize(
    ("kind", "value"), [("url", "https://jobs.example.com/123"), ("company", "Example Labs")]
)
async def test_a_later_takedown_spares_manual_listings(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    db: ManualDb,
    drains: list[int],
    kind: str,
    value: str,
) -> None:
    _, headers = student
    body = (await _add(client, headers, FULL)).json()
    pool = db.add("job-1", url=FULL["url"], company="Example Labs")
    async with AsyncSession(engine) as session:
        assert await listings_service.block(session, kind, value) == 1
        await session.commit()
    assert not db.exists(pool)
    assert db.exists(uuid.UUID(body["listing_id"]))
    assert (await client.get(f"{APPLICATIONS}/{body['id']}", headers=headers)).json()[
        "listing_available"
    ] is True


async def test_a_url_takedown_works_beside_a_urlless_manual_listing(
    client: AsyncClient, engine: AsyncEngine, student: Student, db: ManualDb, drains: list[int]
) -> None:
    body = (await _add(client, student[1], {"title": "Data Intern"})).json()
    pool = db.add("job-1")
    async with AsyncSession(engine) as session:
        removed = await listings_service.block(
            session, "url", "https://www.linkedin.com/jobs/view/job-1"
        )
        await session.commit()
    assert removed == 1 and not db.exists(pool)
    assert db.exists(uuid.UUID(body["listing_id"]))


# --------------------------------------------------------------------------- expiry


async def test_an_expired_manual_listing_still_shows_as_added_by_you(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    body = (await _add(client, headers, {**FULL, "deadline": "2000-01-02"})).json()
    listing_id = uuid.UUID(body["listing_id"])
    # The expire job never expires a pinned Listing (its Application pins it)...
    await listings_service.expire_listings()
    assert not db.expired(listing_id)
    # ...but if it has expired anyway (e.g. pinned only later), the owner still sees it as theirs.
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (listing_id,))
    app = (await client.get(f"{APPLICATIONS}/{body['id']}", headers=headers)).json()
    assert app["listing_available"] is False
    assert app["is_manual"] is True and app["description"] == FULL["description"]
    assert app["deadline"] == "2000-01-02"  # the snapshot
    row = (await client.get(APPLICATIONS, headers=headers)).json()["items"][0]
    assert row["is_manual"] is True and row["in_pool"] is False and row["score"] is None


# --------------------------------------------------------------------------- no fetch


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Record (and refuse) any outbound connection: sockets, DNS, httpx and urllib. The
    database driver (libpq) does not use Python sockets, and the test client talks to the
    app in-process (ASGITransport)."""
    attempts: list[str] = []

    def refuse(what: str):  # noqa: ANN202
        def _refused(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            attempts.append(what)
            raise OSError(f"network access attempted in a test ({what})")

        return _refused

    monkeypatch.setattr(socket.socket, "connect", refuse("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("socket.connect_ex"))
    monkeypatch.setattr(socket, "create_connection", refuse("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("socket.getaddrinfo"))
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", refuse("httpx.AsyncHTTPTransport")
    )
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse("httpx.HTTPTransport"))
    monkeypatch.setattr(urllib.request, "urlopen", refuse("urllib.urlopen"))
    yield attempts


async def test_the_url_is_never_fetched(
    client: AsyncClient,
    student: Student,
    db: ManualDb,
    drains: list[int],
    no_network: list[str],
) -> None:
    owner, headers = student
    for url in ("https://jobs.example.com/123", "http://127.0.0.1:9/x", "https://localhost/a"):
        resp = await _add(client, headers, {"url": url})
        assert resp.status_code == 201, resp.text
    assert no_network == []
    assert len(db.manual_rows(owner)) == 3


# --------------------------------------------------------------------------- privacy


async def test_another_student_never_sees_it(
    client: AsyncClient,
    student: Student,
    other_student: Student,
    db: ManualDb,
    drains: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.matching import router as matching_router

    async def no_drain() -> None:
        return None

    monkeypatch.setattr(matching_router, "drain_priority", no_drain)
    owner, headers = student
    other, other_headers = other_student
    db.grant_pool(owner)  # even a pool-contributing Student's manual job stays private
    for who in (owner, other):
        db.set_profile(who, ["python", "docker"])
        db.set_preferences(who, ["Platform Intern"])
    body = (await _add(client, headers, FULL)).json()
    listing_id = body["listing_id"]
    await recompute(owner, priority=True)
    await recompute(other, priority=True)
    assert uuid.UUID(listing_id) in db.scores(owner)
    assert db.scores(other) == {}

    browse = await client.get(LISTINGS, headers=other_headers)
    assert listing_id not in [i["id"] for i in browse.json()["items"]]
    for q in ("Platform Intern", SECRET_WORD, "Example Labs"):
        found = await client.get(f"{LISTINGS}/search", params={"q": q}, headers=other_headers)
        assert listing_id not in [i["id"] for i in found.json()["items"]]
    assert (await client.get(f"{LISTINGS}/{listing_id}", headers=other_headers)).status_code == 404
    feed = await client.get("/api/v1/feed", headers=other_headers)
    assert feed.status_code == 200
    assert listing_id not in [i["id"] for i in feed.json()["items"]]
    matches = await client.get("/api/v1/matches", params={"ids": [listing_id]}, headers=other_headers)
    assert matches.json()["items"] == []
    match = await client.get(f"{LISTINGS}/{listing_id}/match", headers=other_headers)
    assert match.status_code == 404
    saved = await client.post(APPLICATIONS, json={"listing_id": listing_id}, headers=other_headers)
    assert saved.status_code == 404
    # Nor their Application.
    assert (
        await client.get(f"{APPLICATIONS}/{body['id']}", headers=other_headers)
    ).status_code == 404
    ((_, _, _, visible_to, _, _),) = db.manual_rows(owner)
    assert visible_to == owner


async def test_the_description_is_never_logged(
    client: AsyncClient,
    student: Student,
    db: ManualDb,
    drains: list[int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    resp = await _add(client, student[1], FULL)
    assert resp.status_code == 201
    # 4,100 code points: within the request schema's cap, over the service's limit.
    text = f"{SECRET_WORD} " + "x" * (4100 - len(SECRET_WORD) - 1)
    assert len(text) == 4100
    refused = await _add(client, student[1], {**FULL, "description": text})
    assert _error_loc(refused) == ["body", "description"]
    assert SECRET_WORD not in refused.text
    assert SECRET_WORD not in caplog.text
    assert all(SECRET_WORD not in str(r.__dict__) for r in caplog.records)


# --------------------------------------------------------------------------- delete


async def test_deleting_the_application_deletes_the_manual_listing(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    db.set_profile(owner, ["python"])
    body = (await _add(client, headers, FULL)).json()
    listing_id = uuid.UUID(body["listing_id"])
    await recompute(owner, priority=True)
    assert listing_id in db.scores(owner)
    resp = await client.delete(f"{APPLICATIONS}/{body['id']}", headers=headers)
    assert resp.status_code == 204
    assert not db.exists(listing_id)
    assert db.scores(owner) == {}
    assert db.application(body["id"]) is None
    # Added again after deleting: a new Listing and Application.
    again = await _add(client, headers, FULL)
    assert again.status_code == 201 and again.json()["id"] != body["id"]


async def test_deleting_a_pool_application_never_deletes_the_listing(
    client: AsyncClient, student: Student, db: ManualDb
) -> None:
    owner, headers = student
    pool = db.add("job-1")
    private = db.add("job-2", visibility_owner_id=owner)  # collected privately, not manual
    for listing in (pool, private):
        app = await save(client, headers, listing)
        assert app["is_manual"] is False and app["description"] is None
        resp = await client.delete(f"{APPLICATIONS}/{app['id']}", headers=headers)
        assert resp.status_code == 204
        assert db.exists(listing) and db.pin_count(listing) == 0


async def test_account_deletion_cascades(
    client: AsyncClient, student: Student, db: ManualDb, drains: list[int]
) -> None:
    owner, headers = student
    body = (await _add(client, headers, FULL)).json()
    db.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert not db.exists(uuid.UUID(body["listing_id"]))
    assert db.application(body["id"]) is None


# --------------------------------------------------------------------------- auth, schema


async def test_signed_out_is_401(client: AsyncClient, db: ManualDb) -> None:
    resp = await client.post(MANUAL, json=FULL)
    assert resp.status_code == 401
    assert db.manual_count() == 0


def test_pool_listings_cannot_hold_a_description_or_lack_a_url(db: ManualDb) -> None:
    listing = db.add("job-1")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "UPDATE app.listings SET private_description = 'x' WHERE id = %s", (listing,)
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE app.listings SET url = NULL WHERE id = %s", (listing,))
    manual = db.add("manual-1", source="manual")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "UPDATE app.listings SET private_description = repeat('x', 4001) WHERE id = %s",
            (manual,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE app.listings SET url = 'ftp://x' WHERE id = %s", (manual,))
    db.execute("UPDATE app.listings SET url = NULL WHERE id = %s", (manual,))


def test_only_a_manual_draft_may_leave_out_the_url() -> None:
    with pytest.raises(ValidationError):
        ListingDraft(source="linkedin", source_job_id="job-1", title="Backend Intern")
    draft = ListingDraft(source="manual", source_job_id="m-1", title="Backend Intern")
    assert draft.url is None
