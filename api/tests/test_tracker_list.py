"""FR-17, AD-7: the Tracker list, upcoming deadlines and the batch saved-state lookup
(Story 4.2). One test (or more) per API row of the I/O matrix."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import clock
from app.modules.tracker import repo, service
from tests.maintenance_fixtures import mdb  # noqa: F401 (fixture)
from tests.tracker_fixtures import (  # noqa: F401 (fixtures)
    APPLICATIONS,
    PROBLEM,
    Student,
    TrackerDb,
    other_student,
    save,
    student,
    tdb,
)

pytestmark = pytest.mark.usefixtures("clean_jobs")

DEADLINES = f"{APPLICATIONS}/deadlines"
BY_LISTINGS = f"{APPLICATIONS}/by-listings"


def _set(tdb: TrackerDb, application_id: str, **values: object) -> None:
    """Write Application columns directly (test setup only; the service never does this)."""
    sets = ", ".join(f"{k} = %s" for k in values)
    tdb.execute(
        f"UPDATE app.applications SET {sets} WHERE id = %s", (*values.values(), application_id)
    )


def _age(tdb: TrackerDb, application_id: str, minutes: int) -> None:
    """Make ``created_at`` ``minutes`` older than now (ordering control)."""
    tdb.execute(
        "UPDATE app.applications SET created_at = now() - %s * interval '1 minute' "
        "WHERE id = %s",
        (minutes, application_id),
    )


def _store_score(tdb: TrackerDb, owner: uuid.UUID, listing_id: uuid.UUID, value: int) -> None:
    breakdown = (
        '{"skills_matched": [], "missing_skills": [], "components": ['
        f'{{"name": "skills", "points": {value}, "max_points": 70}}, '
        '{"name": "text", "points": 0, "max_points": 30}]}'
    )
    tdb.execute(
        "INSERT INTO app.match_scores (owner_id, listing_id, score, breakdown, algo_version, "
        "weights_version, vocab_version, computed_at) "
        "VALUES (%s, %s, %s, CAST(%s AS jsonb), 1, 1, 'test', now())",
        (owner, listing_id, value, breakdown),
    )


async def _saved(
    client: AsyncClient,
    student: Student,
    tdb: TrackerDb,
    job: str,
    *,
    company: str = "Acme Corp",
    deadline: str | None = None,
) -> tuple[uuid.UUID, str]:
    listing = tdb.add(job, company=company, deadline=deadline)
    body = await save(client, student[1], listing)
    return listing, body["id"]


async def _list(client: AsyncClient, headers: dict[str, str], params: object = None) -> dict:
    resp = await client.get(APPLICATIONS, params=params, headers=headers)  # type: ignore[arg-type]
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- list


async def test_no_filters_pages_by_created_at_with_a_total(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    ids = []
    for n in range(25):
        _, app_id = await _saved(client, student, tdb, f"job-{n}")
        _age(tdb, app_id, 100 - n)  # the last saved is the most recent
        ids.append(app_id)
    expected = list(reversed(ids))

    first = await _list(client, headers)
    assert first["total"] == 25
    assert [i["id"] for i in first["items"]] == expected[:20]
    assert first["next_cursor"]
    second = await _list(client, headers, {"cursor": first["next_cursor"]})
    assert second["total"] == 25
    assert [i["id"] for i in second["items"]] == expected[20:]
    assert second["next_cursor"] is None

    item = first["items"][0]
    assert item["in_pool"] is True and item["listing_available"] is True
    assert item["score"] is None
    assert item["status"] == "saved" and item["allowed_next"] == ["applied", "withdrawn"]


async def test_a_row_changed_between_pages_appears_exactly_once(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    ids = []
    for n in range(5):
        _, app_id = await _saved(client, student, tdb, f"job-{n}")
        _age(tdb, app_id, 100 - n)
        ids.append(app_id)
    expected = list(reversed(ids))
    first = await _list(client, headers, {"limit": 2})
    assert [i["id"] for i in first["items"]] == expected[:2]
    # A row of a later page changes Status (updated_at bumps) before the next page loads.
    resp = await client.post(
        f"{APPLICATIONS}/{expected[3]}/status", json={"status": "applied"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    seen = [i["id"] for i in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = await _list(client, headers, {"limit": 2, "cursor": cursor})
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
    assert seen == expected


async def test_limit_is_capped_and_an_invalid_cursor_is_a_422(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    for params in ({"cursor": "not-a-cursor"}, {"limit": 51}, {"limit": 0}):
        resp = await client.get(APPLICATIONS, params=params, headers=headers)
        assert resp.status_code == 422, params
        assert resp.headers["content-type"].startswith(PROBLEM)


async def test_status_filter_is_any_of_and_total_matches(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    statuses = ["saved", "applied", "interview", "rejected", "applied"]
    for n, status in enumerate(statuses):
        _, app_id = await _saved(client, student, tdb, f"job-{n}")
        _set(tdb, app_id, status=status)
    body = await _list(client, headers, [("status", "applied"), ("status", "interview")])
    assert body["total"] == 3
    assert sorted(i["status"] for i in body["items"]) == ["applied", "applied", "interview"]

    resp = await client.get(APPLICATIONS, params={"status": "pending"}, headers=headers)
    assert resp.status_code == 422


async def test_company_is_a_trimmed_case_insensitive_substring(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    await _saved(client, student, tdb, "job-1", company="Acme Corp")
    await _saved(client, student, tdb, "job-2", company="ACME")
    await _saved(client, student, tdb, "job-3", company="Globex")
    await _saved(client, student, tdb, "job-4", company="100% Labs")
    body = await _list(client, headers, {"company": "  acme "})
    assert body["total"] == 2
    assert sorted(i["company"] for i in body["items"]) == ["ACME", "Acme Corp"]
    # LIKE wildcards are matched literally.
    assert (await _list(client, headers, {"company": "%"}))["total"] == 1

    for company in ("x" * 101, "   "):
        resp = await client.get(APPLICATIONS, params={"company": company}, headers=headers)
        assert resp.status_code == 422, company


async def test_combined_filters_and_the_saved_date_range(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    rows = [
        ("job-1", "Acme Corp", "applied", "2026-03-10T06:00:00Z"),  # match
        ("job-2", "Acme Corp", "saved", "2026-03-10T06:00:00Z"),  # wrong status
        ("job-3", "Globex", "applied", "2026-03-10T06:00:00Z"),  # wrong company
        ("job-4", "Acme Corp", "applied", "2026-04-10T06:00:00Z"),  # out of range
        ("job-5", "Acme Corp", "interview", "2026-03-01T06:00:00Z"),  # match (from bound)
    ]
    for job, company, status, created in rows:
        _, app_id = await _saved(client, student, tdb, job, company=company)
        _set(tdb, app_id, status=status, created_at=created)
    params = [
        ("status", "applied"),
        ("status", "interview"),
        ("company", "acme"),
        ("date_from", "2026-03-01"),
        ("date_to", "2026-03-31"),
    ]
    body = await _list(client, headers, params)
    assert body["total"] == 2
    assert sorted(i["status"] for i in body["items"]) == ["applied", "interview"]

    resp = await client.get(
        APPLICATIONS,
        params={"date_from": "2026-03-02", "date_to": "2026-03-01"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_applied_date_range_excludes_rows_without_applied_on(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    _, inside = await _saved(client, student, tdb, "job-1")
    _set(tdb, inside, status="applied", applied_on=date(2026, 5, 5))
    _, outside = await _saved(client, student, tdb, "job-2")
    _set(tdb, outside, status="applied", applied_on=date(2026, 6, 5))
    await _saved(client, student, tdb, "job-3")  # never applied
    params = {"date_by": "applied", "date_from": "2026-05-01", "date_to": "2026-05-31"}
    body = await _list(client, headers, params)
    assert [i["id"] for i in body["items"]] == [inside]
    assert body["total"] == 1
    # Only a lower bound: still excludes the row without applied_on.
    body = await _list(client, headers, {"date_by": "applied", "date_from": "2026-01-01"})
    assert body["total"] == 2


async def test_saved_date_uses_the_students_time_zone(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    tdb.set_timezone(owner, "Asia/Kolkata")
    _, app_id = await _saved(client, student, tdb, "job-1")
    # 23:30 IST on 10 March is 18:00 UTC on 10 March; 00:30 IST on 11 March is still
    # 10 March in UTC.
    _set(tdb, app_id, created_at="2026-03-10T19:00:00Z")  # 00:30 IST, 11 March
    ist_11 = {"date_from": "2026-03-11", "date_to": "2026-03-11"}
    utc_10 = {"date_from": "2026-03-10", "date_to": "2026-03-10"}
    assert (await _list(client, headers, ist_11))["total"] == 1
    assert (await _list(client, headers, utc_10))["total"] == 0
    _set(tdb, app_id, created_at="2026-03-10T18:00:00Z")  # 23:30 IST, 10 March
    assert (await _list(client, headers, utc_10))["total"] == 1
    assert (await _list(client, headers, ist_11))["total"] == 0


async def test_saved_date_follows_a_non_default_time_zone(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    tdb.set_timezone(owner, "America/New_York")
    _, app_id = await _saved(client, student, tdb, "job-1")
    # US daylight time starts on 8 March 2026: 02:00 UTC on 11 March is 22:00 EDT on 10 March.
    _set(tdb, app_id, created_at="2026-03-11T02:00:00Z")
    day_10 = {"date_from": "2026-03-10", "date_to": "2026-03-10"}
    day_11 = {"date_from": "2026-03-11", "date_to": "2026-03-11"}
    assert (await _list(client, headers, day_10))["total"] == 1
    assert (await _list(client, headers, day_11))["total"] == 0
    # Open-ended bounds use the same local midnight.
    assert (await _list(client, headers, {"date_to": "2026-03-10"}))["total"] == 1
    assert (await _list(client, headers, {"date_from": "2026-03-11"}))["total"] == 0
    # 04:00 UTC on 11 March is midnight in New York: 11 March, inclusive start.
    _set(tdb, app_id, created_at="2026-03-11T04:00:00Z")
    assert (await _list(client, headers, day_11))["total"] == 1
    assert (await _list(client, headers, day_10))["total"] == 0


async def test_purged_listing_is_listed_from_the_snapshot(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    listing, app_id = await _saved(client, student, tdb, "job-1", deadline="2026-12-31")
    _store_score(tdb, owner, listing, 55)
    tdb.execute("UPDATE app.listings SET pin_count = 0 WHERE id = %s", (listing,))
    tdb.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    (item,) = (await _list(client, headers))["items"]
    assert item["id"] == app_id and item["listing_id"] is None
    assert item["in_pool"] is False and item["listing_available"] is False
    assert item["deadline"] == "2026-12-31"
    assert item["score"] is None
    assert item["title"] == "Backend Intern"


@pytest.mark.parametrize("how", ["expired", "private"])
async def test_expired_or_hidden_listing_uses_the_snapshot_without_a_score(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb, how: str
) -> None:
    owner, headers = student
    listing, _ = await _saved(client, student, tdb, "job-1", deadline="2026-12-31")
    _store_score(tdb, owner, listing, 70)
    tdb.execute("UPDATE app.listings SET deadline = '2027-01-15' WHERE id = %s", (listing,))
    if how == "expired":
        tdb.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (listing,))
    else:
        tdb.execute(
            "UPDATE app.listings SET visibility_owner_id = %s WHERE id = %s",
            (other_student[0], listing),
        )
    (item,) = (await _list(client, headers))["items"]
    assert item["in_pool"] is False
    assert item["deadline"] == "2026-12-31"
    assert item["score"] is None


async def test_available_listing_uses_its_current_deadline(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing, app_id = await _saved(client, student, tdb, "job-1", deadline="2026-12-31")
    tdb.execute("UPDATE app.listings SET deadline = '2027-01-15' WHERE id = %s", (listing,))
    (item,) = (await _list(client, headers))["items"]
    assert item["deadline"] == "2027-01-15"
    assert tdb.application(app_id)["deadline"] == date(2026, 12, 31)  # snapshot untouched
    detail = (await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)).json()
    assert detail["deadline"] == "2027-01-15"
    patched = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"notes": "n"}, headers=headers
    )
    assert patched.status_code == 200 and patched.json()["deadline"] == "2027-01-15"
    moved = await client.post(
        f"{APPLICATIONS}/{app_id}/status", json={"status": "applied"}, headers=headers
    )
    assert moved.status_code == 200 and moved.json()["deadline"] == "2027-01-15"
    # Saving again returns the existing Application with the effective deadline too.
    again = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert again.status_code == 200 and again.json()["deadline"] == "2027-01-15"


async def test_save_response_carries_the_effective_deadline(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing = tdb.add("job-1", deadline="2027-01-15")
    body = await save(client, headers, listing)
    assert body["deadline"] == "2027-01-15"


async def test_a_listing_without_a_deadline_keeps_the_snapshot_deadline(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing, app_id = await _saved(client, student, tdb, "job-1", deadline="2026-12-31")
    tdb.execute("UPDATE app.listings SET deadline = NULL WHERE id = %s", (listing,))
    (item,) = (await _list(client, headers))["items"]
    assert item["in_pool"] is True and item["deadline"] == "2026-12-31"
    detail = (await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)).json()
    assert detail["deadline"] == "2026-12-31"


async def test_stored_score_is_shown_and_missing_is_null(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    scored, scored_app = await _saved(client, student, tdb, "job-1")
    _, plain_app = await _saved(client, student, tdb, "job-2")
    _store_score(tdb, owner, scored, 64)
    scores = {i["id"]: i["score"] for i in (await _list(client, headers))["items"]}
    assert scores == {scored_app: 64, plain_app: None}
    # Never computed or stored by the Tracker.
    assert tdb.execute(
        "SELECT count(*) FROM app.match_scores WHERE owner_id = %s", (owner,)
    ) == [(1,)]


async def test_closed_rows_carry_their_reopen_target(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    _, app_id = await _saved(client, student, tdb, "job-1")
    for status in ("applied", "rejected"):
        resp = await client.post(
            f"{APPLICATIONS}/{app_id}/status", json={"status": status}, headers=headers
        )
        assert resp.status_code == 200, resp.text
    (item,) = (await _list(client, headers))["items"]
    assert item["status"] == "rejected" and item["allowed_next"] == ["applied"]


async def test_closed_rows_use_the_latest_closing_on_a_shared_page(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    _, cycled = await _saved(client, student, tdb, "job-1")
    _, plain = await _saved(client, student, tdb, "job-2")

    async def to(app_id: str, status: str) -> None:
        resp = await client.post(
            f"{APPLICATIONS}/{app_id}/status", json={"status": status}, headers=headers
        )
        assert resp.status_code == 200, resp.text

    # Withdrawn from Saved, reopened to Saved, then Applied and Withdrawn again.
    for status in ("withdrawn", "saved", "applied", "withdrawn"):
        await to(cycled, status)
    for status in ("applied", "rejected"):
        await to(plain, status)
    allowed = {i["id"]: i["allowed_next"] for i in (await _list(client, headers))["items"]}
    assert allowed == {cycled: ["applied"], plain: ["applied"]}


async def test_other_students_applications_never_appear(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing = tdb.add("job-1")
    await save(client, other_student[1], listing)
    assert (await _list(client, headers)) == {"items": [], "total": 0, "next_cursor": None}
    assert (await client.get(DEADLINES, headers=headers)).json() == {"items": []}
    resp = await client.get(BY_LISTINGS, params={"ids": str(listing)}, headers=headers)
    assert resp.json() == {"items": []}


async def test_signed_out_is_a_401(client: AsyncClient) -> None:
    for path in (APPLICATIONS, DEADLINES, BY_LISTINGS):
        assert (await client.get(path)).status_code == 401, path


# --------------------------------------------------------------------------- query count


@contextmanager
def _counting(engine: AsyncEngine) -> Iterator[list[str]]:
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)


async def _queries_for_page(engine: AsyncEngine, owner: uuid.UUID) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        with _counting(engine) as statements:
            page = await service.list_applications(session, owner, limit=20)
        await session.rollback()
    assert page.items
    return len(statements)


async def test_a_page_costs_a_fixed_number_of_queries(
    client: AsyncClient, engine: AsyncEngine, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student

    async def add_rows(start: int, count: int) -> None:
        for n in range(start, start + count):
            listing, app_id = await _saved(client, student, tdb, f"job-{n}")
            _store_score(tdb, owner, listing, 40 + n)
            if n % 3 == 0:
                _set(tdb, app_id, status="withdrawn")  # a closed row needs its history
                tdb.execute(
                    "INSERT INTO app.application_status_history (id, application_id, owner_id, "
                    "from_status, to_status, changed_at) VALUES (%s, %s, %s, 'saved', "
                    "'withdrawn', now())",
                    (uuid.uuid7(), app_id, owner),
                )

    await add_rows(1, 2)  # no closed rows: the history query is skipped
    assert await _queries_for_page(engine, owner) <= 6
    await add_rows(0, 1)
    small = await _queries_for_page(engine, owner)
    await add_rows(3, 22)
    large = await _queries_for_page(engine, owner)
    # preferences, page, count, closed history, availability + deadlines, stored scores.
    assert small == large <= 6


# --------------------------------------------------------------------------- deadlines


async def test_upcoming_deadlines_within_the_window_soonest_first(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    today = clock.local_day(None)

    def day(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    _, in_10 = await _saved(client, student, tdb, "job-1", deadline=day(10))
    _, in_3 = await _saved(client, student, tdb, "job-2", deadline=day(3))
    await _saved(client, student, tdb, "job-3", deadline=day(40))
    await _saved(client, student, tdb, "job-4", deadline=day(-1))
    _, offer = await _saved(client, student, tdb, "job-5", deadline=day(5))
    _set(tdb, offer, status="offer")
    _, applied_today = await _saved(client, student, tdb, "job-6", deadline=day(0))
    _set(tdb, applied_today, status="applied")
    await _saved(client, student, tdb, "job-7")  # no deadline

    resp = await client.get(DEADLINES, headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["id"] for i in items] == [applied_today, in_3, in_10]
    first = items[0]
    assert set(first) == {"id", "title", "company", "status", "deadline", "days_left", "in_pool"}
    assert first["days_left"] == 0 and first["status"] == "applied" and first["in_pool"] is True
    assert [i["days_left"] for i in items] == [0, 3, 10]

    wide = (await client.get(DEADLINES, params={"days": 45}, headers=headers)).json()["items"]
    assert len(wide) == 4
    for days in (0, 181):
        resp = await client.get(DEADLINES, params={"days": days}, headers=headers)
        assert resp.status_code == 422, days


async def test_upcoming_deadlines_use_the_effective_or_snapshot_deadline(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    today = clock.local_day(None)
    soon, far = (today + timedelta(days=n) for n in (2, 60))
    # In the pool: the Listing's deadline moved closer, so it is upcoming now.
    moved, moved_app = await _saved(client, student, tdb, "job-1", deadline=far.isoformat())
    tdb.execute("UPDATE app.listings SET deadline = %s WHERE id = %s", (soon, moved))
    # Purged: the snapshot deadline still counts.
    gone, gone_app = await _saved(client, student, tdb, "job-2", deadline=soon.isoformat())
    tdb.execute("UPDATE app.listings SET pin_count = 0 WHERE id = %s", (gone,))
    tdb.execute("DELETE FROM app.listings WHERE id = %s", (gone,))
    items = (await client.get(DEADLINES, headers=headers)).json()["items"]
    by_id = {i["id"]: i for i in items}
    assert set(by_id) == {moved_app, gone_app}
    assert by_id[moved_app]["deadline"] == soon.isoformat() and by_id[moved_app]["in_pool"]
    assert by_id[gone_app]["in_pool"] is False


async def test_upcoming_deadlines_follow_the_students_time_zone(
    client: AsyncClient, student: Student, tdb: TrackerDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, headers = student
    tdb.set_timezone(owner, "Pacific/Pago_Pago")  # UTC-11
    # 20:00 UTC on 10 March: 09:00 on 10 March in Pago Pago, already 11 March in India.
    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 3, 10, 20, tzinfo=UTC))
    _, local_today = await _saved(client, student, tdb, "job-1", deadline="2026-03-10")
    _, local_tomorrow = await _saved(client, student, tdb, "job-2", deadline="2026-03-11")
    await _saved(client, student, tdb, "job-3", deadline="2026-03-09")
    items = (await client.get(DEADLINES, headers=headers)).json()["items"]
    assert [(i["id"], i["days_left"]) for i in items] == [(local_today, 0), (local_tomorrow, 1)]


async def test_upcoming_deadlines_skip_snapshot_rows_outside_the_window_in_sql(
    client: AsyncClient, engine: AsyncEngine, student: Student, tdb: TrackerDb
) -> None:
    owner, _ = student
    today = clock.local_day(None)
    inside, outside = (today + timedelta(days=n) for n in (5, 90))
    for job, deadline in (("job-1", inside), ("job-2", outside)):
        listing, _ = await _saved(client, student, tdb, job, deadline=deadline.isoformat())
        tdb.execute("UPDATE app.listings SET pin_count = 0 WHERE id = %s", (listing,))
        tdb.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    async with AsyncSession(engine) as session:
        rows = await repo.deadline_candidates(
            session,
            owner,
            service.DEADLINE_STATUSES,
            first=today,
            last=today + timedelta(days=30),
            limit=service.MAX_DEADLINE_CANDIDATES,
        )
    assert [r.deadline for r in rows] == [inside]


async def test_upcoming_deadlines_sort_ties_by_title_and_cap_at_20(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    deadline = (clock.local_day(None) + timedelta(days=4)).isoformat()
    for n in range(22):
        _, app_id = await _saved(client, student, tdb, f"job-{n}", deadline=deadline)
        _set(tdb, app_id, title=f"Role {n:02d}")
    items = (await client.get(DEADLINES, headers=headers)).json()["items"]
    assert [i["title"] for i in items] == [f"Role {n:02d}" for n in range(20)]


# --------------------------------------------------------------------------- by-listings


async def test_batch_saved_state_returns_only_saved_listings(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listings = [tdb.add(f"job-{n}") for n in range(20)]
    saved = {}
    for listing in listings[:3]:
        saved[str(listing)] = (await save(client, headers, listing))["id"]
    params = [("ids", str(i)) for i in listings] + [("ids", str(listings[0]))]
    resp = await client.get(BY_LISTINGS, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert {i["listing_id"]: i["application_id"] for i in items} == saved
    assert len(items) == 3

    empty = await client.get(BY_LISTINGS, headers=headers)
    assert empty.status_code == 200 and empty.json() == {"items": []}


async def test_batch_saved_state_over_50_distinct_ids_is_a_422(
    client: AsyncClient, student: Student
) -> None:
    _, headers = student
    ids = [f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 52)]
    resp = await client.get(BY_LISTINGS, params=[("ids", i) for i in ids], headers=headers)
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith(PROBLEM)
    # 50 distinct ids (with duplicates) are fine.
    params = [("ids", i) for i in ids[:50]] * 2
    assert (await client.get(BY_LISTINGS, params=params, headers=headers)).status_code == 200
