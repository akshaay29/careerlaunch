"""FR-10, NFR-4, UJ-1: ``GET /api/v1/feed``, my ranked feed (Story 3.3)."""

import base64
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.modules.matching import router as matching_router
from tests.conftest import AccountFactory, TokenFactory
from tests.listing_fixtures import draft
from tests.match_fixtures import MatchDb, ingest, mark, recompute

pytestmark = pytest.mark.usefixtures("clean_jobs")

FEED = "/api/v1/feed"
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


@pytest.fixture
def drains(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the after-response drain with a counter (the default for these tests)."""
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    monkeypatch.setattr(matching_router, "drain_priority", counted)
    return calls


def _setup(db: MatchDb, owner: uuid.UUID, *, threshold: int | None = None) -> None:
    db.set_preferences(owner, ["Backend"])
    db.set_profile(owner, ["python"])
    if threshold is not None:
        db.execute(
            "UPDATE app.preferences SET threshold = %s WHERE owner_id = %s", (threshold, owner)
        )


def _put_score(db: MatchDb, owner: uuid.UUID, listing_id: uuid.UUID, value: int) -> None:
    db.execute(
        "INSERT INTO app.match_scores (owner_id, listing_id, score, breakdown, algo_version, "
        "weights_version, vocab_version, computed_at) "
        "VALUES (%s, %s, %s, '{}'::jsonb, 1, 1, 'test', now())",
        (owner, listing_id, value),
    )


async def _listings(
    engine: AsyncEngine,
    db: MatchDb,
    count: int,
    *,
    start: int = 1,
    posted: Sequence[datetime | None] | None = None,
) -> list[uuid.UUID]:
    drafts = [
        draft(n, posted_at=posted[i] if posted is not None else None)
        for i, n in enumerate(range(start, start + count))
    ]
    await ingest(engine, drafts)
    return [db.id_of(f"job-{n}") for n in range(start, start + count)]


def _ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


def _score_map(body: dict) -> dict[str, int]:
    return {item["id"]: item["score"] for item in body["items"]}


# --------------------------------------------------------------------------- ready


@pytest.mark.usefixtures("drains")
async def test_ready_feed_is_ranked_best_first(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    now = datetime.now(UTC)
    a, b, c, d = await _listings(
        engine, db, 4, posted=[now, now - timedelta(days=1), now - timedelta(days=5), now]
    )
    _setup(db, owner)
    for listing_id, value in ((a, 60), (b, 75), (c, 75), (d, 90)):
        _put_score(db, owner, listing_id, value)

    resp = await client.get(FEED, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "ready"
    assert body["threshold"] == 60
    assert body["next_cursor"] is None
    assert _ids(body) == [str(d), str(b), str(c), str(a)]
    assert [i["score"] for i in body["items"]] == [90, 75, 75, 60]
    assert body["hidden_below_threshold"] is False
    first = body["items"][0]
    # ListingOut fields plus score and below_threshold; never the breakdown (3.4).
    assert first["source_label"] and first["url"] and first["title"]
    assert first["below_threshold"] is False
    assert "breakdown" not in first


@pytest.mark.usefixtures("drains")
async def test_ties_put_null_posted_at_last_then_first_seen_then_id(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    now = datetime.now(UTC)
    posted, unposted_old, unposted_new, twin_a, twin_b = await _listings(
        engine, db, 5, posted=[now - timedelta(days=30), None, None, None, None]
    )
    seen = now - timedelta(hours=1)
    db.execute(
        "UPDATE app.listings SET first_seen_at = %s WHERE id = %s",
        (seen - timedelta(minutes=10), unposted_old),
    )
    db.execute("UPDATE app.listings SET first_seen_at = %s WHERE id = %s", (seen, unposted_new))
    for twin in (twin_a, twin_b):
        db.execute(
            "UPDATE app.listings SET first_seen_at = %s WHERE id = %s",
            (seen - timedelta(minutes=20), twin),
        )
    _setup(db, owner)
    for listing_id in (posted, unposted_old, unposted_new, twin_a, twin_b):
        _put_score(db, owner, listing_id, 70)

    body = (await client.get(FEED, headers=headers)).json()
    twins = sorted([twin_a, twin_b], reverse=True)
    assert _ids(body) == [str(i) for i in (posted, unposted_new, unposted_old, *twins)]


@pytest.mark.usefixtures("drains")
async def test_threshold_hides_lower_scores_and_the_toggle_shows_them(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    high, low = await _listings(engine, db, 2)
    _setup(db, owner)
    _put_score(db, owner, high, 80)
    _put_score(db, owner, low, 59)

    body = (await client.get(FEED, headers=headers)).json()
    assert body["state"] == "ready"
    assert _ids(body) == [str(high)]
    assert body["hidden_below_threshold"] is True

    body = (await client.get(FEED, params={"show_below_threshold": "true"}, headers=headers)).json()
    assert _ids(body) == [str(high), str(low)]
    assert body["hidden_below_threshold"] is False
    assert [i["below_threshold"] for i in body["items"]] == [False, True]


@pytest.mark.usefixtures("drains")
async def test_custom_threshold(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    a, b, c = await _listings(engine, db, 3)
    _setup(db, owner, threshold=75)
    for listing_id, value in ((a, 90), (b, 75), (c, 74)):
        _put_score(db, owner, listing_id, value)

    body = (await client.get(FEED, headers=headers)).json()
    assert body["threshold"] == 75
    assert _ids(body) == [str(a), str(b)]
    body = (await client.get(FEED, params={"show_below_threshold": True}, headers=headers)).json()
    assert [i["below_threshold"] for i in body["items"]] == [False, False, True]


@pytest.mark.usefixtures("drains")
async def test_everything_below_threshold_is_an_empty_ready_feed(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    (only,) = await _listings(engine, db, 1)
    _setup(db, owner)
    _put_score(db, owner, only, 10)
    body = (await client.get(FEED, headers=headers)).json()
    assert body == {
        "state": "ready",
        "threshold": 60,
        "hidden_below_threshold": True,
        "items": [],
        "next_cursor": None,
    }


@pytest.mark.usefixtures("drains")
async def test_paging_follows_a_stable_keyset(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    now = datetime.now(UTC)
    posted = [None if n % 3 == 0 else now - timedelta(hours=n % 7) for n in range(45)]
    ids = await _listings(engine, db, 45, posted=posted)
    _setup(db, owner)
    for n, listing_id in enumerate(ids):
        _put_score(db, owner, listing_id, 60 + n % 5)  # many ties

    full = (await client.get(FEED, params={"limit": 50}, headers=headers)).json()
    assert len(full["items"]) == 45 and full["next_cursor"] is None

    pages: list[list[str]] = []
    cursor = None
    while True:
        params: dict[str, object] = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get(FEED, params=params, headers=headers)).json()
        pages.append(_ids(body))
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert [len(p) for p in pages] == [20, 20, 5]
    assert [i for p in pages for i in p] == _ids(full)


def _cursor(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        "@@@",
        _cursor("x|2026-01-01T00:00:00+00:00|2026-01-01T00:00:00+00:00|" + str(uuid.UUID(int=1))),
        _cursor("101||2026-01-01T00:00:00+00:00|" + str(uuid.UUID(int=1))),
        _cursor("-1||2026-01-01T00:00:00+00:00|" + str(uuid.UUID(int=1))),
        _cursor("70||2026-01-01T00:00:00|" + str(uuid.UUID(int=1))),
        _cursor("70||2026-01-01T00:00:00+00:00|nope"),
        _cursor("70|2026-01-01T00:00:00+00:00"),
    ],
)
@pytest.mark.usefixtures("drains")
async def test_invalid_cursor_is_422(
    client: AsyncClient, db: MatchDb, student: Student, cursor: str
) -> None:
    owner, headers = student
    _setup(db, owner)
    resp = await client.get(FEED, params={"cursor": cursor}, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)


@pytest.mark.parametrize("limit", [0, 51])
@pytest.mark.usefixtures("drains")
async def test_limit_out_of_range_is_422(
    client: AsyncClient, student: Student, limit: int
) -> None:
    _, headers = student
    resp = await client.get(FEED, params={"limit": limit}, headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


@pytest.mark.usefixtures("drains")
async def test_expired_taken_down_and_now_private_listings_never_show(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    other_student: Student,
) -> None:
    owner, headers = student
    other, _ = other_student
    keep, expired, private_other, own_private, gone = await _listings(engine, db, 5)
    _setup(db, owner)
    for listing_id in (keep, expired, private_other, own_private, gone):
        _put_score(db, owner, listing_id, 80)
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (expired,))
    db.execute(
        "UPDATE app.listings SET visibility_owner_id = %s WHERE id = %s", (other, private_other)
    )
    db.execute(
        "UPDATE app.listings SET visibility_owner_id = %s WHERE id = %s", (owner, own_private)
    )
    db.execute("DELETE FROM app.listings WHERE id = %s", (gone,))
    # The stale score rows are still there (except the deleted Listing's, which cascaded).
    assert set(db.scores(owner)) == {keep, expired, private_other, own_private}

    body = (await client.get(FEED, headers=headers)).json()
    assert body["state"] == "ready"
    assert set(_ids(body)) == {str(keep), str(own_private)}


@pytest.mark.usefixtures("drains")
async def test_another_students_scores_are_never_visible(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    other_student: Student,
) -> None:
    owner, headers = student
    other, other_headers = other_student
    mine, theirs = await _listings(engine, db, 2)
    _setup(db, owner)
    _setup(db, other)
    _put_score(db, owner, mine, 70)
    _put_score(db, other, theirs, 95)
    _put_score(db, other, mine, 99)

    body = (await client.get(FEED, headers=headers)).json()
    assert body["items"] == [body["items"][0]] and _ids(body) == [str(mine)]
    assert body["items"][0]["score"] == 70
    other_body = (await client.get(FEED, headers=other_headers)).json()
    assert _score_map(other_body) == {str(mine): 99, str(theirs): 95}


async def test_signed_out_is_401(client: AsyncClient) -> None:
    resp = await client.get(FEED)
    assert resp.status_code == 401


# --------------------------------------------------------------------------- setup needed


async def test_setup_needed_without_preferences_or_skills(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    (listing,) = await _listings(engine, db, 1)
    # Even a leftover score row is not read: setup decides first.
    _put_score(db, owner, listing, 90)
    body = (await client.get(FEED, headers=headers)).json()
    assert body == {
        "state": "setup_needed",
        "threshold": 60,
        "hidden_below_threshold": False,
        "items": [],
        "next_cursor": None,
    }

    # An empty Preferences row and an empty Skill Profile are still no setup.
    db.set_preferences(owner, [])
    db.set_profile(owner, [])
    db.execute("UPDATE app.preferences SET threshold = 70 WHERE owner_id = %s", (owner,))
    body = (await client.get(FEED, headers=headers)).json()
    assert body == {
        "state": "setup_needed",
        "threshold": 70,
        "hidden_below_threshold": False,
        "items": [],
        "next_cursor": None,
    }
    assert db.outbox() == [] and drains == []


# --------------------------------------------------------------------------- provisional


async def test_provisional_feed_scores_top_candidates_on_request(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    ids = await _listings(engine, db, 6)
    db.set_skills(ids[0], ["python"])
    db.set_skills(ids[1], ["python", "docker"])
    db.set_skills(ids[2], ["java", "docker", "aws", "kubernetes"])
    await ingest(engine, [draft(50, title="Pastry Chef")])
    chef = db.id_of("job-50")
    _setup(db, owner, threshold=0)

    # threshold 0 and a limit above the candidate count: every candidate is shown.
    resp = await client.get(FEED, params={"limit": 10}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "provisional"
    assert body["next_cursor"] is None
    assert len(body["items"]) == 6
    assert str(chef) not in _ids(body)
    # The same scorer as the on-request endpoint.
    for item in body["items"]:
        match = await client.get(f"/api/v1/listings/{item['id']}/match", headers=headers)
        assert match.json()["score"] == item["score"]
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert body["items"][0]["id"] == str(ids[0])
    # Nothing stored; one priority recompute queued and the drain started.
    assert db.scores(owner) == {}
    assert [(key, queue) for key, queue, _ in db.outbox()] == [
        (f"matching.recompute:student:{owner}:priority", "priority")
    ]
    assert [c for o, c in db.ops("matching.marked_stale") if o == owner] == [
        {"feed_first_open": 1}
    ]
    assert drains == [1]

    # Opening again while that priority job is pending queues nothing more.
    body = (await client.get(FEED, params={"limit": 10}, headers=headers)).json()
    assert body["state"] == "provisional"
    assert len(db.outbox()) == 1 and drains == [1]


@pytest.mark.usefixtures("drains")
async def test_provisional_applies_the_threshold_rule(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    good, weak = await _listings(engine, db, 2)
    db.set_skills(good, ["python"])
    db.set_skills(weak, ["java", "docker", "aws", "kubernetes", "react"])
    _setup(db, owner)

    shown = (await client.get(FEED, params={"show_below_threshold": True}, headers=headers)).json()
    scores = _score_map(shown)
    assert set(scores) == {str(good), str(weak)}
    assert scores[str(good)] >= 60 > scores[str(weak)]
    assert [i["below_threshold"] for i in shown["items"]] == [False, True]

    assert shown["hidden_below_threshold"] is False
    hidden = (await client.get(FEED, headers=headers)).json()
    assert hidden["state"] == "provisional"
    assert _ids(hidden) == [str(good)]
    assert hidden["hidden_below_threshold"] is True


async def test_provisional_with_no_candidates_is_empty(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    await ingest(engine, [draft(1, title="Pastry Chef")])
    _setup(db, owner)
    body = (await client.get(FEED, headers=headers)).json()
    assert body == {
        "state": "provisional",
        "threshold": 60,
        "hidden_below_threshold": False,
        "items": [],
        "next_cursor": None,
    }
    assert len(db.outbox()) == 1


async def test_first_open_turns_ready_after_the_drain(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    """UJ-1: a ranked feed at once, and the real after-response drain stores the scores, so
    the next load is ``ready`` with the same top Listings."""
    owner, headers = student
    ids = await _listings(engine, db, 5)
    db.set_skills(ids[0], ["python"])
    db.set_skills(ids[1], ["python", "docker"])
    _setup(db, owner)

    first = (await client.get(FEED, headers=headers)).json()
    assert first["state"] == "provisional" and first["items"]
    assert db.scores(owner)  # the drain ran after the response

    second = (await client.get(FEED, headers=headers)).json()
    assert second["state"] == "ready"
    assert _score_map(second) == _score_map(first)
    assert _ids(second) == _ids(first)


def _priority_key(owner: uuid.UUID) -> str:
    return f"matching.recompute:student:{owner}:priority"


@pytest.mark.usefixtures("drains")
async def test_scores_only_on_expired_listings_are_provisional(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    old_a, old_b, fresh = await _listings(engine, db, 3)
    _setup(db, owner, threshold=0)
    _put_score(db, owner, old_a, 90)
    _put_score(db, owner, old_b, 80)
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = ANY(%s)", ([old_a, old_b],))
    body = (await client.get(FEED, headers=headers)).json()
    assert body["state"] == "provisional"
    assert _ids(body) == [str(fresh)]


@pytest.mark.usefixtures("drains")
async def test_a_cursor_never_falls_back_to_the_provisional_page(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    a, b, c = await _listings(engine, db, 3)
    _setup(db, owner, threshold=0)
    _put_score(db, owner, a, 90)
    _put_score(db, owner, b, 80)
    first = (await client.get(FEED, params={"limit": 1}, headers=headers)).json()
    assert first["state"] == "ready" and first["next_cursor"]
    # Every stored score's Listing is gone from view; Listing c is still a candidate.
    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = ANY(%s)", ([a, b],))
    body = (
        await client.get(
            FEED, params={"limit": 1, "cursor": first["next_cursor"]}, headers=headers
        )
    ).json()
    assert body == {
        "state": "ready",
        "threshold": 0,
        "hidden_below_threshold": False,
        "items": [],
        "next_cursor": None,
    }
    assert db.outbox() == []


async def test_a_pending_default_job_still_gets_a_priority_job(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    await _listings(engine, db, 2)
    _setup(db, owner)
    await mark(engine, owner, priority=False)
    assert [k for k, _, _ in db.outbox()] == [f"matching.recompute:student:{owner}"]
    body = (await client.get(FEED, headers=headers)).json()
    assert body["state"] == "provisional"
    assert [(k, q) for k, q, _ in db.outbox()][-1] == (_priority_key(owner), "priority")
    assert drains == [1]


async def test_an_old_mark_is_made_again(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    await _listings(engine, db, 2)
    _setup(db, owner)
    await mark(engine, owner, priority=True)
    db.execute(
        "UPDATE app.match_state SET scores_stale_since = now() - interval '20 minutes' "
        "WHERE owner_id = %s",
        (owner,),
    )
    await client.get(FEED, headers=headers)
    assert [k for k, _, _ in db.outbox()] == [_priority_key(owner), _priority_key(owner)]
    assert drains == [1]


async def test_after_a_recompute_that_stored_nothing_no_job_is_queued(
    client: AsyncClient,
    engine: AsyncEngine,
    db: MatchDb,
    student: Student,
    drains: list[int],
) -> None:
    owner, headers = student
    _setup(db, owner)  # no Listing in the pool: no candidate
    await mark(engine, owner)
    await recompute(owner)
    assert db.scores(owner) == {}
    db.clear_outbox()
    for _ in range(2):
        body = (await client.get(FEED, headers=headers)).json()
        assert body["state"] == "provisional"
        assert db.outbox() == []
    assert drains == []


@pytest.fixture
def cap_two(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FEED_ON_REQUEST_CAP", "2")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.usefixtures("drains", "cap_two")
async def test_provisional_scores_at_most_the_cap_newest_first(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    owner, headers = student
    ids = await _listings(engine, db, 4)
    base = datetime.now(UTC) - timedelta(days=1)
    for n, listing_id in enumerate(ids):  # ids[3] newest
        db.execute(
            "UPDATE app.listings SET first_seen_at = %s WHERE id = %s",
            (base + timedelta(minutes=n), listing_id),
        )
    _setup(db, owner, threshold=0)
    body = (await client.get(FEED, headers=headers)).json()
    assert body["state"] == "provisional"
    assert len(body["items"]) <= 2
    assert set(_ids(body)) == {str(ids[2]), str(ids[3])}


async def test_provisional_order_matches_the_ready_order(
    client: AsyncClient, engine: AsyncEngine, db: MatchDb, student: Student
) -> None:
    """Tied scores with mixed ``posted_at``: the on-request sort equals the stored feed's."""
    owner, headers = student
    now = datetime.now(UTC)
    posted = [now - timedelta(days=1), None, now - timedelta(days=2), None]
    ids = await _listings(engine, db, 4, posted=posted)
    seen = now - timedelta(hours=1)
    # The NULL-posted ids[1] was first seen after every other Listing.
    for listing_id, minutes in zip(ids, (0, 30, 10, 5), strict=True):
        db.execute(
            "UPDATE app.listings SET first_seen_at = %s WHERE id = %s",
            (seen + timedelta(minutes=minutes), listing_id),
        )
    for listing_id in ids:
        db.set_skills(listing_id, ["python"])
    _setup(db, owner, threshold=0)

    first = (await client.get(FEED, headers=headers)).json()
    assert first["state"] == "provisional"
    assert len(set(_score_map(first).values())) == 1  # all tied
    assert _ids(first) == [str(i) for i in (ids[0], ids[2], ids[1], ids[3])]
    second = (await client.get(FEED, headers=headers)).json()
    assert second["state"] == "ready"
    assert _ids(second) == _ids(first)
