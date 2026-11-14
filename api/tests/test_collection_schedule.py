"""Story 2.3: a saved Provider Key schedules daily collection and a first run; the scheduler
tick, the ``listings.collect`` job, and the end-to-end path to ``GET /api/v1/listings``."""

import uuid
from zoneinfo import ZoneInfo
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.core.clock import local_day, next_local_time
from app.core.jobs import scheduler_tick
from app.main import create_app
from app.modules.listings import service, tasks
from app.ports import provider_runner as provider_runner_port
from app.ports.provider_runner import RunState
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.provider_runner import FakeProviderRunner
from tests.listing_fixtures import ListingDb, listing_db  # noqa: F401 (fixture)
from tests.provider_run_fixtures import (  # noqa: F401 (fixtures)
    TOKEN,
    RunsDb,
    curious_dataset,
    make_student,
    master_key,
    runner,
    runs_db,
)

pytestmark = pytest.mark.usefixtures("clean_jobs", "listing_db")

KEY_URL = "/api/v1/provider-key"


@pytest.fixture
def app(runner: FakeProviderRunner, tokens: TokenFactory):  # noqa: ARG001 (env order)
    saved = provider_runner_port._installed
    yield create_app(provider_runner=runner)
    provider_runner_port._installed = saved


def _headers(accounts: AccountFactory, tokens: TokenFactory, owner: uuid.UUID) -> dict[str, str]:
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    return tokens.headers(tokens.token(owner, email))


def _schedule(db: RunsDb, owner: uuid.UUID) -> tuple:
    (row,) = db.execute(
        'SELECT kind, paused, next_run_at, "interval" FROM app.schedules WHERE owner_id = %s',
        (owner,),
    )
    return row


# --------------------------------------------------------------------------- clock


def test_next_local_time() -> None:
    at = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)  # 01:30 on the 15th in Kolkata
    assert next_local_time("Asia/Kolkata", 6, at) == datetime(2026, 11, 15, 0, 30, tzinfo=UTC)
    at = datetime(2026, 11, 15, 0, 30, tzinfo=UTC)  # exactly 06:00 local: the next day
    assert next_local_time("Asia/Kolkata", 6, at) == datetime(2026, 11, 16, 0, 30, tzinfo=UTC)
    assert next_local_time(None, 6, at) == datetime(2026, 11, 16, 0, 30, tzinfo=UTC)


def test_local_day() -> None:
    at = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)  # 01:30 next day in Kolkata
    assert local_day("Asia/Kolkata", at) == date(2026, 11, 15)
    assert local_day(None, at) == date(2026, 11, 15)
    assert local_day("Not/AZone", at) == date(2026, 11, 15)
    assert local_day("America/Los_Angeles", at) == date(2026, 11, 14)
    assert local_day("UTC", datetime(2026, 11, 14, 23, 59)) == date(2026, 11, 14)  # naive = UTC


# --------------------------------------------------------------------------- key saved


async def test_key_saved_schedules_and_enqueues(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    owner = await make_student(accounts, runs_db, key=False)
    runs_db.set_preferences(owner, ["SDE"], timezone="America/Los_Angeles")
    before = datetime.now(UTC)
    resp = await client.put(
        KEY_URL, json={"token": TOKEN}, headers=_headers(accounts, tokens, owner)
    )
    assert resp.status_code == 200, resp.text
    kind, paused, next_run_at, interval = _schedule(runs_db, owner)
    assert kind == "listings.collect" and paused is False and interval == timedelta(days=1)
    # Anchored to 06:00 in the Student's time zone, within the next day.
    local = next_run_at.astimezone(ZoneInfo("America/Los_Angeles"))
    assert (local.hour, local.minute) == (6, 0)
    assert before < next_run_at <= before + timedelta(days=1)
    (job,) = runs_db.outbox(tasks.COLLECT)
    day = local_day("America/Los_Angeles").isoformat()
    assert job[0] == f"listings.collect:student:{owner}:{day}"
    assert job[2] == {"owner_id": str(owner)}
    # Saving enqueues only: no run is started inside the request.
    assert runner.started == [] and runs_db.runs(owner) == []


async def test_saving_again_resumes_a_paused_schedule(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
) -> None:
    owner = await make_student(accounts, runs_db, key=False)
    headers = _headers(accounts, tokens, owner)
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200
    assert (await client.delete(KEY_URL, headers=headers)).status_code == 204
    assert _schedule(runs_db, owner)[1] is True  # paused by accounts
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200
    assert _schedule(runs_db, owner)[1] is False
    assert len(runs_db.execute("SELECT id FROM app.schedules WHERE owner_id = %s", (owner,))) == 1
    # Same local day: the same key (a second outbox row dedupes in Procrastinate, and the
    # run_key makes a second collect a no-op).
    keys = {k for k, _, _ in runs_db.outbox(tasks.COLLECT)}
    assert len(keys) == 1


async def test_tick_enqueues_due_collection(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runs_db.execute("DELETE FROM app.job_outbox")
    runs_db.execute(
        "UPDATE app.schedules SET next_run_at = now() - interval '1 minute' WHERE owner_id = %s",
        (owner,),
    )
    assert await scheduler_tick() == 1
    (job,) = runs_db.outbox(tasks.COLLECT)
    assert job[0].startswith(f"listings.collect:owner:{owner}:")
    assert job[2] == {"owner_id": str(owner)}
    assert _schedule(runs_db, owner)[2] > datetime.now(UTC)


# --------------------------------------------------------------------------- collect job


async def test_collect_starts_a_scoped_run(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runs_db.set_preferences(
        owner, ["Data Analyst", "SDE"], ["Pune"], ["internship", "full_time"], "Asia/Tokyo"
    )
    await tasks.collect(owner_id=owner)
    (row,) = runs_db.runs(owner)
    assert row["status"] == "running" and row["apify_run_id"] == "run1"
    assert row["run_key"] == f"collect:{owner}:{local_day('Asia/Tokyo').isoformat()}"
    assert row["params"] == {
        "role_titles": ["Data Analyst", "SDE"],
        "locations": ["Pune"],
        "job_types": ["internship", "full_time"],
    }
    (call,) = runner.started
    assert len(call.run_input["urls"]) == 2
    assert len(runs_db.outbox(tasks.POLL_PROVIDER_RUN)) == 1
    # The tick and a key save on the same day: one Apify start.
    await tasks.collect(owner_id=owner)
    assert len(runner.started) == 1


async def test_collect_without_role_titles_is_skipped(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db, role_titles=[])
    await tasks.collect(owner_id=owner)
    no_prefs = accounts.create()
    await tasks.collect(owner_id=no_prefs)
    assert runner.started == [] and runs_db.runs(owner) == [] and runs_db.runs(no_prefs) == []
    assert [(o, c) for o, _, c in runs_db.ops("listings.collect_skipped")] == [
        (owner, {"no_preferences": 1}),
        (no_prefs, {"no_preferences": 1}),
    ]


# --------------------------------------------------------------------------- acceptance


async def test_key_saved_then_drains_put_listings_in_the_feed(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    owner = await make_student(accounts, runs_db, key=False, pool=False)
    headers = _headers(accounts, tokens, owner)
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200

    # Drain 1: the enqueued collect job.
    ((_, _, args),) = runs_db.outbox(tasks.COLLECT)
    await tasks.collect(owner_id=uuid.UUID(args["owner_id"]))
    # Drain 2: the poll job (the run is still going), drain 3: it succeeded.
    ((_, _, poll_args),) = runs_db.outbox(tasks.POLL_PROVIDER_RUN)
    run_id = uuid.UUID(poll_args["provider_run_id"])
    runner.queue(RunState("RUNNING", 0.001, None), RunState("SUCCEEDED", 0.009, "ds7"))
    runner.datasets["ds7"] = curious_dataset(5, 1)
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["status"] == "running"
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["status"] == "succeeded"

    resp = await client.get("/api/v1/listings", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 5
    assert {i["title"] for i in items} == {f"Backend Engineering Intern {n}" for n in range(1, 6)}
    # Without pool_contribution they are private to the collector.
    other = accounts.create()
    got = await client.get("/api/v1/listings", headers=_headers(accounts, tokens, other))
    assert got.json()["items"] == []
    assert service.RUN_SUCCEEDED_EVENT in {
        k for (k,) in runs_db.execute("SELECT kind FROM app.ops_events")
    }


async def test_tick_keeps_the_local_hour(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
) -> None:
    owner = await make_student(accounts, runs_db, key=False)  # Asia/Kolkata
    headers = _headers(accounts, tokens, owner)
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200
    first = _schedule(runs_db, owner)[2]
    # The slot comes due; the tick enqueues it and advances by the 24 h interval.
    runs_db.execute(
        "UPDATE app.schedules SET next_run_at = next_run_at - interval '1 day' "
        "WHERE owner_id = %s",
        (owner,),
    )
    assert await scheduler_tick() == 1
    advanced = _schedule(runs_db, owner)[2]
    local = advanced.astimezone(ZoneInfo("Asia/Kolkata"))
    assert (local.hour, local.minute) == (6, 0)
    assert advanced == first


async def test_resaving_the_key_keeps_an_active_schedule(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
) -> None:
    owner = await make_student(accounts, runs_db, key=False)
    headers = _headers(accounts, tokens, owner)
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200
    soon = datetime.now(UTC) + timedelta(minutes=30)
    runs_db.execute(
        "UPDATE app.schedules SET next_run_at = %s WHERE owner_id = %s", (soon, owner)
    )
    assert (await client.put(KEY_URL, json={"token": TOKEN}, headers=headers)).status_code == 200
    kind, paused, next_run_at, _ = _schedule(runs_db, owner)
    assert paused is False and next_run_at == soon  # not pushed back
