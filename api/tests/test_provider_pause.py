"""Story 2.4 / AD-12: Apify auth and credit failures pause the Student's ``provider_status``;
paused Students are refused without an Apify call; exhausted credit resumes after the
billing cycle; a fixed key clears the pause. Schedules are never touched."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from app.core.clock import utcnow
from app.modules.listings import service, tasks
from app.ports.provider_runner import ProviderKeyInvalid, ProviderRejected, RunState
from tests.conftest import AccountFactory
from tests.fakes.provider_runner import FakeProviderRunner
from tests.listing_fixtures import listing_db  # noqa: F401 (fixture)
from tests.provider_run_fixtures import (  # noqa: F401 (fixtures)
    SEED_TOKEN,
    TOKEN,
    RunsDb,
    make_student,
    master_key,
    runner,
    runs_db,
    store_key,
)

pytestmark = pytest.mark.usefixtures("clean_jobs", "listing_db")

PARAMS = {"role_titles": ["Backend Engineering Intern"], "locations": [], "job_types": []}
CYCLE_END = datetime(2026, 12, 1, 23, 59, 59, 999000, tzinfo=UTC)


async def _start(owner: uuid.UUID, day: str) -> service.RunOutcome:
    return await service.start_provider_run(
        owner, "jobs", PARAMS, run_key=f"collect:{owner}:{day}"
    )


def _status(db: RunsDb, owner: uuid.UUID) -> tuple | None:
    rows = db.execute(
        "SELECT status, reason, exhausted_until FROM app.provider_status WHERE owner_id = %s",
        (owner,),
    )
    return rows[0] if rows else None


def _schedule_paused(db: RunsDb, owner: uuid.UUID) -> list[tuple]:
    return db.execute(
        "SELECT paused FROM app.schedules WHERE owner_id = %s AND kind = 'listings.collect'",
        (owner,),
    )


# --------------------------------------------------------------------------- start


async def test_invalid_key_on_start_pauses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.start_error = ProviderKeyInvalid("x")  # type: ignore[assignment]
    outcome = await _start(owner, "d1")
    assert outcome.status == "failed" and outcome.error_kind == "key_invalid"
    assert _status(runs_db, owner) == ("paused", "key_invalid", None)
    # The schedule is untouched; the next tick is refused cheaply.
    assert _schedule_paused(runs_db, owner) == [(False,)]
    runner.start_error = None
    refused = await _start(owner, "d2")
    assert refused.status == "refused" and refused.error_kind == "paused"
    assert len(runner.started) == 1


@pytest.mark.parametrize(
    ("kind", "reason"),
    [("permission", "not_permitted"), ("blocked", "account_blocked")],
)
async def test_rejection_on_start_pauses_with_its_reason(
    kind: str, reason: str, accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.start_error = ProviderRejected("x", kind=kind)  # type: ignore[arg-type,assignment]
    outcome = await _start(owner, "d1")
    assert outcome.error_kind == "rejected"
    assert _status(runs_db, owner) == ("paused", reason, None)
    assert runner.cycle_calls == 0


async def test_credit_on_start_pauses_until_the_cycle_end(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.cycle_end = utcnow() + timedelta(days=5)
    runner.start_error = ProviderRejected("x", kind="credit")  # type: ignore[assignment]
    await _start(owner, "d1")
    assert _status(runs_db, owner) == ("paused", "credit_exhausted", runner.cycle_end)
    assert runner.cycle_calls == 1
    runner.start_error = None
    assert (await _start(owner, "d2")).error_kind == "exhausted"
    assert len(runner.started) == 1


async def test_unavailable_on_start_does_not_pause(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    from app.ports.provider_runner import ProviderUnavailable

    owner = await make_student(accounts, runs_db)
    runner.start_error = ProviderUnavailable("x")  # type: ignore[assignment]
    await _start(owner, "d1")
    assert _status(runs_db, owner) is None


# --------------------------------------------------------------------------- poll


async def _running(owner: uuid.UUID, runner: FakeProviderRunner) -> uuid.UUID:
    outcome = await _start(owner, "d1")
    assert outcome.status == "running", outcome
    return outcome.run_id


async def test_credit_exhausted_on_poll_uses_the_cycle_end(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.cycle_end = utcnow() + timedelta(days=9)
    runner.get_error = ProviderRejected("x", kind="credit")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == "rejected"
    assert _status(runs_db, owner) == ("paused", "credit_exhausted", runner.cycle_end)


@pytest.mark.parametrize("cycle_end", [None, datetime(2020, 1, 1, tzinfo=UTC)])
async def test_unknown_cycle_end_falls_back_to_next_utc_month(
    cycle_end: datetime | None,
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.cycle_end = cycle_end
    runner.get_error = ProviderRejected("x", kind="credit")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    (_, reason, until) = _status(runs_db, owner)  # type: ignore[misc]
    assert reason == "credit_exhausted"
    assert until == service.next_month_start(utcnow())
    assert until.day == 1 and until.hour == 0 and until > utcnow()


async def test_key_invalid_on_poll_and_fetch_pauses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("SUCCEEDED", 0.03, "fakeds0001"))
    runner.fetch_error = ProviderKeyInvalid("x")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["error_kind"] == "key_invalid"
    assert _status(runs_db, owner) == ("paused", "key_invalid", None)


def test_next_month_start_rolls_the_year() -> None:
    dec = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
    assert service.next_month_start(dec) == datetime(2027, 1, 1, tzinfo=UTC)
    assert service.month_start(dec) == datetime(2026, 12, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- resume


async def test_cycle_reset_resumes(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runs_db.execute(
        "INSERT INTO app.provider_status (owner_id, status, reason, exhausted_until) "
        "VALUES (%s, 'paused', 'credit_exhausted', now() - interval '1 minute')",
        (owner,),
    )
    assert (await _start(owner, "d1")).status == "running"


async def test_fixed_key_clears_the_pause(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runs_db.execute(
        "INSERT INTO app.provider_status (owner_id, status, reason) "
        "VALUES (%s, 'paused', 'key_invalid')",
        (owner,),
    )
    assert (await _start(owner, "d1")).error_kind == "paused"
    await store_key(owner)
    assert _status(runs_db, owner) is None
    assert (await _start(owner, "d2")).status == "running"


async def test_seed_runs_never_pause(runs_db: RunsDb, runner: FakeProviderRunner) -> None:
    before = runs_db.execute("SELECT count(*) FROM app.provider_status")
    runner.start_error = ProviderRejected("x", kind="credit")  # type: ignore[assignment]
    outcome = await service.start_provider_run(
        None, "jobs", PARAMS, run_key=f"seed:{uuid.uuid7()}", token=SecretStr(SEED_TOKEN)
    )
    assert outcome.error_kind == "rejected"
    assert runs_db.execute("SELECT count(*) FROM app.provider_status") == before
    assert runner.cycle_calls == 0


async def test_pause_reason_is_checked_by_the_database(
    accounts: AccountFactory, runs_db: RunsDb
) -> None:
    import psycopg

    owner = accounts.create()
    with pytest.raises(psycopg.errors.CheckViolation):
        runs_db.execute(
            "INSERT INTO app.provider_status (owner_id, status, reason) "
            "VALUES (%s, 'paused', 'something_else')",
            (owner,),
        )


async def test_no_token_in_status_or_runs(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.start_error = ProviderKeyInvalid("x")  # type: ignore[assignment]
    await _start(owner, "d1")
    assert TOKEN not in repr(runs_db.runs(owner)) + repr(_status(runs_db, owner))


# --------------------------------------------------------------------------- race and stale


async def test_a_pause_still_lands_when_the_run_row_already_failed(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    """The run was reaped (or failed another way) while Apify's refusal was in flight."""
    from tests.provider_run_fixtures import insert_run

    owner = await make_student(accounts, runs_db)
    run_id = insert_run(runs_db, owner, "failed", error_kind="poll_exhausted")
    outcome = await service._fail_run(
        run_id, owner, "rejected", pause=service.Pause("account_blocked")
    )
    assert outcome.status == "failed" and outcome.error_kind == "poll_exhausted"
    assert runs_db.run(run_id)["error_kind"] == "poll_exhausted"  # the row is unchanged
    assert _status(runs_db, owner) == ("paused", "account_blocked", None)


async def test_a_stale_rejection_does_not_pause_after_a_new_key(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    await store_key(owner)  # the Student saves a new key after the run was created
    runner.get_error = ProviderKeyInvalid("old key")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["error_kind"] == "key_invalid"  # the run still fails
    assert _status(runs_db, owner) is None  # but the new key is not paused


async def test_a_rejection_of_the_current_key_still_pauses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)  # the key was saved before the run
    run_id = await _running(owner, runner)
    runner.get_error = ProviderKeyInvalid("x")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert _status(runs_db, owner) == ("paused", "key_invalid", None)
