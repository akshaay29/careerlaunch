"""The Application transition table (FR-16, Story 4.1): every allowed and refused move, the
reopen rule, ``applied_on``, the events, and concurrent transitions."""

import asyncio
import itertools

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import clock
from app.core.errors import InvalidTransitionError
from app.modules.tracker import repo, service
from tests.maintenance_fixtures import mdb  # noqa: F401 (fixture)
from tests.tracker_fixtures import (  # noqa: F401 (fixtures)
    APPLICATIONS,
    PROBLEM,
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

_jobs = itertools.count(1)

ALL = ["saved", "applied", "interview", "offer", "rejected", "withdrawn"]

# Paths (from saved) that reach each Status, and the moves allowed from it.
PATHS = {
    "saved": [],
    "applied": ["applied"],
    "interview": ["applied", "interview"],
    "offer": ["applied", "interview", "offer"],
}
FORWARD = {
    "saved": ["applied", "withdrawn"],
    "applied": ["interview", "rejected", "withdrawn"],
    "interview": ["offer", "rejected", "withdrawn"],
    "offer": [],
}


async def _at(client: AsyncClient, headers: dict[str, str], tdb: TrackerDb, path: list[str]) -> str:
    app_id = (await save(client, headers, tdb.add(f"job-{next(_jobs)}")))["id"]
    for status in path:
        resp = await move(client, headers, app_id, status)
        assert resp.status_code == 200, resp.text
    return app_id


@pytest.mark.parametrize("start", list(FORWARD))
async def test_forward_table(
    client: AsyncClient, student: Student, tdb: TrackerDb, start: str
) -> None:
    """From each open Status: the allowed moves succeed, every other one is a 409 naming
    the allowed ones (the same Status included)."""
    _, headers = student
    allowed = FORWARD[start]
    for target in ALL:
        app_id = await _at(client, headers, tdb, PATHS[start])
        before = tdb.history_rows(app_id)
        resp = await move(client, headers, app_id, target)
        if target in allowed:
            assert resp.status_code == 200, (start, target, resp.text)
            assert resp.json()["status"] == target
            assert tdb.history_rows(app_id) == [*before, (start, target)]
        else:
            assert resp.status_code == 409, (start, target)
            assert resp.headers["content-type"].startswith(PROBLEM)
            body = resp.json()
            assert body["type"] == "/problems/invalid-transition"
            assert body["allowed"] == allowed
            assert tdb.history_rows(app_id) == before
            assert tdb.application(app_id)["status"] == start  # type: ignore[index]


async def test_saved_to_offer_names_the_allowed_moves(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, [])
    resp = await move(client, headers, app_id, "offer")
    assert resp.status_code == 409
    assert resp.json()["allowed"] == ["applied", "withdrawn"]


async def test_same_status_is_409(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, ["applied"])
    resp = await move(client, headers, app_id, "applied")
    assert resp.status_code == 409
    assert resp.json()["allowed"] == ["interview", "rejected", "withdrawn"]


async def test_offer_is_terminal(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, PATHS["offer"])
    got = await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert got.json()["allowed_next"] == []
    for target in ALL:
        resp = await move(client, headers, app_id, target)
        assert resp.status_code == 409 and resp.json()["allowed"] == []


async def test_unknown_status_is_422(client: AsyncClient, student: Student, tdb: TrackerDb) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, [])
    resp = await move(client, headers, app_id, "hired")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("path", "closed", "previous"),
    [
        ([], "withdrawn", "saved"),
        (["applied"], "withdrawn", "applied"),
        (["applied"], "rejected", "applied"),
        (["applied", "interview"], "rejected", "interview"),
        (["applied", "interview"], "withdrawn", "interview"),
    ],
)
async def test_reopen_only_to_the_previous_status(
    client: AsyncClient,
    student: Student,
    tdb: TrackerDb,
    event_log: EventLog,
    path: list[str],
    closed: str,
    previous: str,
) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, [*path, closed])
    got = await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert got.json()["allowed_next"] == [previous]
    for target in ALL:
        if target == previous:
            continue
        resp = await move(client, headers, app_id, target)
        assert resp.status_code == 409, target
        assert resp.json()["allowed"] == [previous]
    resp = await move(client, headers, app_id, previous)
    assert resp.status_code == 200
    assert resp.json()["status"] == previous
    assert resp.json()["allowed_next"] == FORWARD[previous]
    assert event_log.kinds() == ["ApplicationClosed", "ApplicationReopened"]


async def test_reopen_uses_the_latest_closing(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    """Withdrawn from Saved, reopened, applied, withdrawn again: reopen goes to Applied."""
    _, headers = student
    app_id = await _at(client, headers, tdb, ["withdrawn", "saved", "applied", "withdrawn"])
    got = await client.get(f"{APPLICATIONS}/{app_id}", headers=headers)
    assert got.json()["allowed_next"] == ["applied"]
    assert (await move(client, headers, app_id, "saved")).status_code == 409
    assert (await move(client, headers, app_id, "applied")).status_code == 200


async def test_close_publishes_application_closed(
    client: AsyncClient, student: Student, tdb: TrackerDb, event_log: EventLog
) -> None:
    owner, headers = student
    app_id = await _at(client, headers, tdb, ["applied"])
    assert event_log.events == []
    resp = await move(client, headers, app_id, "rejected")
    assert resp.status_code == 200
    (event, seen, in_tx), = event_log.events
    assert type(event).__name__ == "ApplicationClosed"
    assert event.owner_id == owner and str(event.application_id) == app_id  # type: ignore[attr-defined]
    assert seen == "rejected" and in_tx
    assert tdb.history_rows(app_id)[-1] == ("applied", "rejected")


async def test_forward_moves_publish_nothing(
    client: AsyncClient, student: Student, tdb: TrackerDb, event_log: EventLog
) -> None:
    _, headers = student
    await _at(client, headers, tdb, PATHS["offer"])
    assert event_log.events == []


async def test_closed_event_without_a_listing(
    client: AsyncClient, student: Student, tdb: TrackerDb, event_log: EventLog
) -> None:
    _, headers = student
    listing = tdb.add("job-1")
    app_id = (await save(client, headers, listing))["id"]
    tdb.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    assert (await move(client, headers, app_id, "withdrawn")).status_code == 200
    (event, _, _), = event_log.events
    assert event.listing_id is None  # type: ignore[attr-defined]


async def test_a_failing_subscriber_rolls_the_transition_back(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    from app.core.events import ApplicationClosed, clear_subscribers, subscribe

    _, headers = student
    app_id = await _at(client, headers, tdb, ["applied"])

    async def boom(session: AsyncSession, event: ApplicationClosed) -> None:
        raise RuntimeError("subscriber failed")

    subscribe(ApplicationClosed, boom)
    try:
        resp = await move(client, headers, app_id, "rejected")
    finally:
        clear_subscribers(ApplicationClosed)
    assert resp.status_code == 500
    assert tdb.application(app_id)["status"] == "applied"  # type: ignore[index]
    assert tdb.history_rows(app_id)[-1] == ("saved", "applied")


# --------------------------------------------------------------------------- applied_on


async def test_applied_sets_applied_on_in_the_students_time_zone(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    owner, headers = student
    tdb.set_timezone(owner, "Pacific/Kiritimati")
    app_id = await _at(client, headers, tdb, [])
    resp = await move(client, headers, app_id, "applied")
    assert resp.json()["applied_on"] == clock.local_day("Pacific/Kiritimati").isoformat()


async def test_applied_keeps_an_existing_applied_on(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, [])
    await client.patch(f"{APPLICATIONS}/{app_id}", json={"applied_on": "2026-10-01"}, headers=headers)
    resp = await move(client, headers, app_id, "applied")
    assert resp.json()["applied_on"] == "2026-10-01"


async def test_other_moves_leave_applied_on_alone(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    app_id = await _at(client, headers, tdb, [])
    resp = await move(client, headers, app_id, "withdrawn")
    assert resp.json()["applied_on"] is None


async def test_transition_bumps_updated_at(
    client: AsyncClient, student: Student, tdb: TrackerDb
) -> None:
    _, headers = student
    created = await save(client, headers, tdb.add("job-1"))
    resp = await move(client, headers, created["id"], "applied")
    assert resp.json()["updated_at"] > created["updated_at"]
    assert resp.json()["created_at"] == created["created_at"]


# --------------------------------------------------------------------------- concurrency


async def test_concurrent_transitions_serialise(
    engine: AsyncEngine, student: Student, tdb: TrackerDb
) -> None:
    """Two sessions move the same Saved Application to Applied at once: the row lock makes
    the second wait for the first commit, then see Applied and refuse the same Status."""
    owner, _ = student
    async with AsyncSession(engine) as session:
        view, _ = await service.save_listing(session, owner, tdb.add("job-1"))
        app_id = view.application.id
        await session.commit()

    first_locked = asyncio.Event()

    async def first() -> str:
        async with AsyncSession(engine) as session:
            await service.transition(session, owner, app_id, "applied")
            first_locked.set()
            await asyncio.sleep(0.3)  # hold the row lock
            await session.commit()
            return "ok"

    async def second() -> str:
        await first_locked.wait()
        async with AsyncSession(engine) as session:
            try:
                await service.transition(session, owner, app_id, "applied")
            except InvalidTransitionError as exc:
                assert exc.allowed == ["interview", "rejected", "withdrawn"]
                return "refused"
            await session.commit()
            return "ok"

    results = await asyncio.gather(first(), second())
    assert sorted(results) == ["ok", "refused"]
    assert tdb.history_rows(app_id) == [(None, "saved"), ("saved", "applied")]


async def test_concurrent_different_moves_both_land_in_order(
    engine: AsyncEngine, student: Student, tdb: TrackerDb
) -> None:
    """Applied -> Interview and Applied -> Withdrawn at once: the second waits, then moves
    from Interview (Withdrawn is allowed there too). History is a consistent chain."""
    owner, _ = student
    async with AsyncSession(engine) as session:
        view, _ = await service.save_listing(session, owner, tdb.add("job-1"))
        app_id = view.application.id
        await service.transition(session, owner, app_id, "applied")
        await session.commit()
    first_locked = asyncio.Event()

    async def first() -> None:
        async with AsyncSession(engine) as session:
            await service.transition(session, owner, app_id, "interview")
            first_locked.set()
            await asyncio.sleep(0.3)
            await session.commit()

    async def second() -> None:
        await first_locked.wait()
        async with AsyncSession(engine) as session:
            await service.transition(session, owner, app_id, "withdrawn")
            await session.commit()

    await asyncio.gather(first(), second())
    rows = tdb.history_rows(app_id)
    assert rows == [
        (None, "saved"),
        ("saved", "applied"),
        ("applied", "interview"),
        ("interview", "withdrawn"),
    ]
    assert tdb.application(app_id)["status"] == "withdrawn"  # type: ignore[index]


async def test_concurrent_saves_pin_once(
    engine: AsyncEngine, student: Student, tdb: TrackerDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both saves read "not saved yet" before either inserts (a barrier after the first
    lookup): one inserts and pins, the other loses the unique key and returns the winner's
    Application without pinning."""
    owner, _ = student
    listing = tdb.add("job-1")
    barrier = asyncio.Barrier(2)
    real = repo.get_by_listing
    first_reads: set[int] = set()

    async def get_by_listing(session: AsyncSession, *args: object) -> object:
        found = await real(session, *args)  # type: ignore[arg-type]
        if id(session) not in first_reads:
            first_reads.add(id(session))
            await barrier.wait()
        return found

    monkeypatch.setattr(repo, "get_by_listing", get_by_listing)

    async def one() -> tuple[str, bool]:
        async with AsyncSession(engine) as session:
            view, created = await service.save_listing(session, owner, listing)
            app_id = str(view.application.id)
            await session.commit()
            return app_id, created

    results = await asyncio.wait_for(asyncio.gather(one(), one()), timeout=30)
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert tdb.pin_count(listing) == 1
    assert tdb.count_applications(owner) == 1
    assert tdb.history_rows(results[0][0]) == [(None, "saved")]


def test_transition_table_matches_the_spec() -> None:
    assert service.TRANSITIONS == {k: tuple(v) for k, v in FORWARD.items()}
    assert service.CLOSED == {"rejected", "withdrawn"}
    assert set(service.STATUSES) == set(ALL)
