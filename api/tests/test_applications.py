"""FR-16, AD-7, AD-5: saving a Listing as an Application, its details, deletion and
isolation (Story 4.1). One test (or more) per row of the I/O matrix; the transition table
itself is in ``test_application_transitions.py``."""

import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import clock
from app.modules.tracker import service
from tests.maintenance_fixtures import mdb  # noqa: F401 (fixture)
from tests.tracker_fixtures import (  # noqa: F401 (fixtures)
    APPLICATIONS,
    PROBLEM,
    UNKNOWN_ID,
    EventLog,
    Student,
    TrackerDb,
    event_log,
    move,
    other_student,
    save,
    student,
    tdb,
)

pytestmark = pytest.mark.usefixtures("clean_jobs")

OUT_FIELDS = {
    "id",
    "listing_id",
    "listing_available",
    "status",
    "allowed_next",
    "title",
    "company",
    "url",
    "deadline",
    "notes",
    "applied_on",
    "resume_id",
    "created_at",
    "updated_at",
    "today",
}


# --------------------------------------------------------------------------- save


async def test_save_creates_a_saved_application_with_a_snapshot_and_a_pin(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    listing = tdb.add("job-1", company="Acme Corp", deadline="2026-12-31")
    resp = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == OUT_FIELDS
    assert body["status"] == "saved"
    assert body["allowed_next"] == ["applied", "withdrawn"]
    assert body["listing_id"] == str(listing) and body["listing_available"] is True
    assert body["title"] == "Backend Intern" and body["company"] == "Acme Corp"
    assert body["url"] == "https://www.linkedin.com/jobs/view/job-1"
    assert body["deadline"] == "2026-12-31"
    assert body["notes"] is None and body["applied_on"] is None and body["resume_id"] is None
    assert body["today"] == clock.local_day(None).isoformat()
    assert tdb.history_rows(body["id"]) == [(None, "saved")]
    assert tdb.pin_count(listing) == 1
    assert tdb.count_applications(owner) == 1


async def test_save_twice_returns_the_same_application_without_pinning_again(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    listing = tdb.add("job-1")
    first = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    second = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert tdb.pin_count(listing) == 1
    assert tdb.count_applications(owner) == 1
    assert tdb.history_rows(first.json()["id"]) == [(None, "saved")]


async def test_save_twice_keeps_a_later_status(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing = tdb.add("job-1")
    app_id = (await save(client, headers, listing))["id"]
    assert (await move(client, headers, app_id, "applied")).status_code == 200
    again = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "applied"


async def test_two_students_save_the_same_listing_separately(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    listing = tdb.add("job-1")
    a = await save(client, student[1], listing)
    b = await save(client, other_student[1], listing)
    assert a["id"] != b["id"]
    assert tdb.pin_count(listing) == 2


@pytest.mark.parametrize("case", ["expired", "private_other", "unknown"])
async def test_save_invisible_is_404_and_writes_nothing(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb, case: str
) -> None:
    owner, headers = student
    if case == "expired":
        listing = str(tdb.add("job-1", expired_days_ago=1))
    elif case == "private_other":
        listing = str(tdb.add("job-1", visibility_owner_id=other_student[0]))
    else:
        listing = UNKNOWN_ID
    resp = await client.post(APPLICATIONS, json={"listing_id": listing}, headers=headers)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert tdb.count_applications(owner) == 0
    if case != "unknown":
        assert tdb.pin_count(uuid.UUID(listing)) == 0


async def test_save_my_own_private_listing(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    listing = tdb.add("job-1", visibility_owner_id=owner)
    resp = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert resp.status_code == 201


# --------------------------------------------------------------------------- details


async def test_details_are_saved(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    owner, headers = student
    resume = tdb.resume(owner)
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    resp = await client.patch(
        f"{APPLICATIONS}/{app_id}",
        json={"notes": "  Spoke to the recruiter.  ", "applied_on": "2026-11-01", "resume_id": str(resume)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notes"] == "Spoke to the recruiter."
    assert body["applied_on"] == "2026-11-01"
    assert body["resume_id"] == str(resume)
    assert body["status"] == "saved"
    row = tdb.application(app_id)
    assert row is not None and row["notes"] == "Spoke to the recruiter."
    assert row["status"] == "saved"


async def test_patch_changes_only_the_fields_sent(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    resume = tdb.resume(owner)
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    await client.patch(
        f"{APPLICATIONS}/{app_id}",
        json={"notes": "First", "applied_on": "2026-11-01", "resume_id": str(resume)},
        headers=headers,
    )
    resp = await client.patch(f"{APPLICATIONS}/{app_id}", json={"notes": "Second"}, headers=headers)
    body = resp.json()
    assert body["notes"] == "Second"
    assert body["applied_on"] == "2026-11-01" and body["resume_id"] == str(resume)


async def test_blank_notes_clear_them(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    await client.patch(f"{APPLICATIONS}/{app_id}", json={"notes": "x"}, headers=headers)
    resp = await client.patch(f"{APPLICATIONS}/{app_id}", json={"notes": "   "}, headers=headers)
    assert resp.json()["notes"] is None
    resp = await client.patch(f"{APPLICATIONS}/{app_id}", json={"notes": "y"}, headers=headers)
    resp = await client.patch(f"{APPLICATIONS}/{app_id}", json={"notes": None}, headers=headers)
    assert resp.json()["notes"] is None


async def test_notes_limit(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    ok = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"notes": " " + "a" * 4000 + " "}, headers=headers
    )
    assert ok.status_code == 200 and len(ok.json()["notes"]) == 4000
    too_long = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"notes": "a" * 4001}, headers=headers
    )
    assert too_long.status_code == 422
    assert too_long.headers["content-type"].startswith(PROBLEM)
    assert too_long.json()["errors"][0]["loc"] == ["body", "notes"]
    assert len(tdb.application(app_id)["notes"]) == 4000  # type: ignore[index]


async def test_future_applied_date_is_422(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    tdb.set_timezone(owner, "Pacific/Kiritimati")  # UTC+14: "today" is ahead of UTC
    today = clock.local_day("Pacific/Kiritimati")
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    ok = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"applied_on": today.isoformat()}, headers=headers
    )
    assert ok.status_code == 200
    future = await client.patch(
        f"{APPLICATIONS}/{app_id}",
        json={"applied_on": (today + timedelta(days=1)).isoformat()},
        headers=headers,
    )
    assert future.status_code == 422
    assert future.json()["errors"][0]["loc"] == ["body", "applied_on"]
    assert ok.json()["today"] == today.isoformat()
    for early in ("1999-12-31", "0026-05-01"):
        too_early = await client.patch(
            f"{APPLICATIONS}/{app_id}", json={"applied_on": early}, headers=headers
        )
        assert too_early.status_code == 422, early
        assert too_early.json()["errors"][0]["loc"] == ["body", "applied_on"]
    assert (
        await client.patch(f"{APPLICATIONS}/{app_id}", json={"applied_on": "2000-01-01"}, headers=headers)
    ).status_code == 200
    cleared = await client.patch(f"{APPLICATIONS}/{app_id}", json={"applied_on": None}, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["applied_on"] is None


async def test_another_students_resume_is_422(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    theirs = tdb.resume(other_student[0])
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    for resume in (str(theirs), UNKNOWN_ID):
        resp = await client.patch(
            f"{APPLICATIONS}/{app_id}", json={"resume_id": resume}, headers=headers
        )
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == ["body", "resume_id"]
    assert tdb.application(app_id)["resume_id"] is None  # type: ignore[index]


async def test_resume_deleted_during_the_update_is_422(
    client: AsyncClient, student: Student, tdb: TrackerDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ownership check passes but the Resume is gone by the write (foreign key): 422,
    and nothing changes."""
    _, headers = student
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]

    async def owns(*_: object) -> bool:
        return True

    monkeypatch.setattr(service.profile_service, "owns_resume", owns)
    resp = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"notes": "n", "resume_id": UNKNOWN_ID}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["loc"] == ["body", "resume_id"]
    row = tdb.application(app_id)
    assert row is not None and row["resume_id"] is None and row["notes"] is None


async def test_clear_resume(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    owner, headers = student
    resume = tdb.resume(owner)
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    await client.patch(f"{APPLICATIONS}/{app_id}", json={"resume_id": str(resume)}, headers=headers)
    resp = await client.patch(f"{APPLICATIONS}/{app_id}", json={"resume_id": None}, headers=headers)
    assert resp.status_code == 200 and resp.json()["resume_id"] is None


async def test_patch_never_changes_status(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    resp = await client.patch(
        f"{APPLICATIONS}/{app_id}", json={"status": "offer", "notes": "n"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"
    assert tdb.history_rows(app_id) == [(None, "saved")]


async def test_deleted_resume_clears_the_choice(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    resume = tdb.resume(owner)
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    await client.patch(f"{APPLICATIONS}/{app_id}", json={"resume_id": str(resume)}, headers=headers)
    tdb.execute("DELETE FROM app.resumes WHERE id = %s", (resume,))
    resp = await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert resp.json()["resume_id"] is None


# --------------------------------------------------------------------------- delete


async def test_delete_removes_history_and_unpins(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    listing = tdb.add("job-1")
    app_id = (await save(client, headers, listing))["id"]
    await move(client, headers, app_id, "applied")
    assert tdb.pin_count(listing) == 1
    resp = await client.delete(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert resp.status_code == 204
    assert tdb.application(app_id) is None
    assert tdb.history_rows(app_id) == []
    assert tdb.pin_count(listing) == 0
    assert (await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)).status_code == 404
    # The Listing can be saved again.
    again = await client.post(APPLICATIONS, json={"listing_id": str(listing)}, headers=headers)
    assert again.status_code == 201 and tdb.pin_count(listing) == 1
    assert tdb.count_applications(owner) == 1


async def test_delete_unknown_is_404(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    resp = await client.delete(f"{APPLICATIONS}/{UNKNOWN_ID}", headers=student[1])
    assert resp.status_code == 404


# --------------------------------------------------------------------------- Listing purged


async def test_listing_purged_keeps_the_snapshot(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    listing = tdb.add("job-1", company="Gone Inc", deadline="2026-12-01")
    app_id = (await save(client, headers, listing))["id"]
    tdb.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    assert tdb.pin_count(other := tdb.add("job-2")) == 0
    resp = await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["listing_id"] is None and body["listing_available"] is False
    assert body["title"] == "Backend Intern" and body["company"] == "Gone Inc"
    assert body["url"] == "https://www.linkedin.com/jobs/view/job-1"
    assert body["deadline"] == "2026-12-01"
    # Status still works without the Listing.
    assert (await move(client, headers, app_id, "applied")).status_code == 200
    # Delete skips the unpin.
    assert (await client.delete(f"{APPLICATIONS}/{app_id}", headers=headers)).status_code == 204
    assert tdb.pin_count(other) == 0


async def test_listing_expired_or_made_private_is_not_available(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    expired = tdb.add("job-1")
    private = tdb.add("job-2")
    open_one = tdb.add("job-3")
    ids = {k: (await save(client, headers, v))["id"] for k, v in
           {"expired": expired, "private": private, "open": open_one}.items()}
    tdb.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (expired,))
    tdb.execute(
        "UPDATE app.listings SET visibility_owner_id = %s WHERE id = %s",
        (other_student[0], private),
    )
    for key, available in (("expired", False), ("private", False), ("open", True)):
        body = (await client.get(f"{APPLICATIONS}/{ids[key]}", headers=headers)).json()
        assert body["listing_available"] is available, key
        assert body["listing_id"] is not None


async def test_delete_publishes_closed_unless_already_closed(
    client: AsyncClient, student: Student, tdb: TrackerDb, event_log: EventLog
) -> None:
    owner, headers = student
    listing = tdb.add("job-1")
    open_id = (await save(client, headers, listing))["id"]
    closed_id = (await save(client, headers, tdb.add("job-2")))["id"]
    assert (await move(client, headers, closed_id, "withdrawn")).status_code == 200
    event_log.events.clear()

    assert (await client.delete(f"{APPLICATIONS}/{open_id}", headers=headers)).status_code == 204
    (event, seen, in_tx), = event_log.events
    assert type(event).__name__ == "ApplicationClosed"
    assert event.owner_id == owner and str(event.application_id) == open_id  # type: ignore[attr-defined]
    assert event.listing_id == listing  # type: ignore[attr-defined]
    assert seen == "saved" and in_tx  # published before the row is deleted

    assert (await client.delete(f"{APPLICATIONS}/{closed_id}", headers=headers)).status_code == 204
    assert len(event_log.events) == 1


async def test_account_deletion_releases_the_pins(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    saved = tdb.add("job-1", pins=2)
    shared = tdb.add("job-2")
    purged = tdb.add("job-3")
    await save(client, headers, saved)
    await save(client, headers, shared)
    await save(client, other_student[1], shared)
    await save(client, headers, purged)
    tdb.execute("DELETE FROM app.listings WHERE id = %s", (purged,))
    assert (tdb.pin_count(saved), tdb.pin_count(shared)) == (3, 2)

    _, email = tdb.execute("SELECT id, email FROM app.accounts WHERE id = %s", (owner,))[0]
    try:
        resp = await client.request(
            "DELETE", "/api/v1/me", json={"confirm_email": email}, headers=headers
        )
        assert resp.status_code == 204, resp.text
    finally:
        tdb.execute("DELETE FROM app.deleted_accounts WHERE id = %s", (owner,))
    assert tdb.count_applications(owner) == 0
    assert (tdb.pin_count(saved), tdb.pin_count(shared)) == (2, 1)


# --------------------------------------------------------------------------- history


async def test_history_newest_first(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    for status in ("applied", "interview", "offer"):
        assert (await move(client, headers, app_id, status)).status_code == 200
    resp = await client.get(f"{APPLICATIONS}/{app_id}/history", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [(i["from_status"], i["to_status"]) for i in items] == [
        ("interview", "offer"),
        ("applied", "interview"),
        ("saved", "applied"),
        (None, "saved"),
    ]
    stamps = [i["changed_at"] for i in items]
    assert stamps == sorted(stamps, reverse=True)


# --------------------------------------------------------------------------- by listing


async def test_by_listing(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    saved = tdb.add("job-1")
    unsaved = tdb.add("job-2")
    app_id = (await save(client, headers, saved))["id"]
    resp = await client.get(f"{APPLICATIONS}/by-listing/{saved}", headers=headers)
    assert resp.status_code == 200 and resp.json() == {"application_id": app_id}
    resp = await client.get(f"{APPLICATIONS}/by-listing/{unsaved}", headers=headers)
    assert resp.json() == {"application_id": None}
    # Another Student does not see my Application.
    resp = await client.get(f"{APPLICATIONS}/by-listing/{saved}", headers=other_student[1])
    assert resp.json() == {"application_id": None}


# --------------------------------------------------------------------------- isolation


async def test_another_students_application_is_404_everywhere(
    client: AsyncClient, student: Student, other_student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    resume = tdb.resume(other_student[0])
    app_id = (await save(client, headers, tdb.add("job-1")))["id"]
    theirs = other_student[1]
    base = f"{APPLICATIONS}/{app_id}"
    for resp in (
        await client.get(base, headers=theirs),
        await client.patch(base, json={"notes": "x", "resume_id": str(resume)}, headers=theirs),
        await client.post(f"{base}/status", json={"status": "applied"}, headers=theirs),
        await client.get(f"{base}/history", headers=theirs),
        await client.delete(base, headers=theirs),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["type"] == "/problems/not-found"
    row = tdb.application(app_id)
    assert row is not None and row["status"] == "saved" and row["notes"] is None
    assert tdb.count_applications(owner) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", APPLICATIONS),
        ("GET", f"{APPLICATIONS}/{UNKNOWN_ID}"),
        ("PATCH", f"{APPLICATIONS}/{UNKNOWN_ID}"),
        ("POST", f"{APPLICATIONS}/{UNKNOWN_ID}/status"),
        ("DELETE", f"{APPLICATIONS}/{UNKNOWN_ID}"),
        ("GET", f"{APPLICATIONS}/{UNKNOWN_ID}/history"),
        ("GET", f"{APPLICATIONS}/by-listing/{UNKNOWN_ID}"),
    ],
)
async def test_signed_out_is_401(client: AsyncClient, method: str, path: str) -> None:
    resp = await client.request(method, path, json={})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


# --------------------------------------------------------------------------- acceptance


async def test_save_apply_reject_reopen(
    client: AsyncClient, student: Student, tdb: TrackerDb, event_log: EventLog
) -> None:
    """Save, mark applied, then rejected, then reopen: every Status is in the history newest
    first, the Listing is pinned, and the events ran inside the transitions' transactions."""
    owner, headers = student
    listing = tdb.add("job-1")
    app_id = (await save(client, headers, listing))["id"]
    applied = await move(client, headers, app_id, "applied")
    assert applied.json()["applied_on"] == clock.local_day(None).isoformat()
    rejected = await move(client, headers, app_id, "rejected")
    assert rejected.json()["allowed_next"] == ["applied"]
    reopened = await move(client, headers, app_id, "applied")
    assert reopened.status_code == 200 and reopened.json()["status"] == "applied"

    items = (await client.get(f"{APPLICATIONS}/{app_id}/history", headers=headers)).json()["items"]
    assert [i["to_status"] for i in items] == ["applied", "rejected", "applied", "saved"]
    assert tdb.pin_count(listing) == 1

    assert event_log.kinds() == ["ApplicationClosed", "ApplicationReopened"]
    (closed, closed_seen, closed_tx), (reopen, reopen_seen, reopen_tx) = event_log.events
    assert closed.owner_id == owner and str(closed.application_id) == app_id  # type: ignore[attr-defined]
    assert closed.listing_id == listing  # type: ignore[attr-defined]
    # The publisher's session already sees the new Status: same transaction, before commit.
    assert closed_seen == "rejected" and closed_tx
    assert reopen_seen == "applied" and reopen_tx
    assert date.fromisoformat(reopened.json()["applied_on"]) == date.fromisoformat(
        applied.json()["applied_on"]
    )
