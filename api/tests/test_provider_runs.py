"""Story 2.3 / AD-12: the one Apify spend path (``start_provider_run``), the poll job and the
seed script, on the FakeProviderRunner. One test (or more) per row of the I/O matrix."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.modules.listings import service, tasks
from app.ports.provider_runner import ProviderKeyInvalid, ProviderRejected, ProviderUnavailable
from app.ports.provider_runner import RunState
from tests.conftest import AccountFactory
from tests.fakes.provider_runner import FakeProviderRunner
from tests.listing_fixtures import ListingDb, listing_db  # noqa: F401 (fixture)
from tests.provider_run_fixtures import (  # noqa: F401 (fixtures)
    DESCRIPTION_WORD,
    PRIMARY,
    SEED_TOKEN,
    TOKEN,
    RunsDb,
    curious_dataset,
    curious_item,
    make_student,
    master_key,
    runner,
    runs_db,
)

pytestmark = pytest.mark.usefixtures("clean_jobs", "listing_db")

PARAMS = {
    "role_titles": ["Backend Engineering Intern"],
    "locations": ["Bengaluru, Karnataka, India"],
    "job_types": ["internship"],
}


def _key(owner: uuid.UUID, day: str = "2026-11-14") -> str:
    return f"collect:{owner}:{day}"


async def _start(owner: uuid.UUID, run_key: str | None = None) -> service.RunOutcome:
    return await service.start_provider_run(owner, "jobs", PARAMS, run_key=run_key or _key(owner))


async def _running(owner: uuid.UUID, fake: FakeProviderRunner) -> uuid.UUID:
    outcome = await _start(owner)
    assert outcome.status == "running", outcome
    return outcome.run_id


# --------------------------------------------------------------------------- start


async def test_collect_starts_a_run(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    outcome = await _start(owner)
    assert outcome.status == "running" and outcome.error_kind is None

    (call,) = runner.started
    assert call.token == TOKEN and call.actor == PRIMARY
    assert call.max_items == 100 and call.max_charge_usd == 0.25
    assert call.run_input["limitPerSource"] == 100
    (url,) = call.run_input["urls"]
    assert "keywords=Backend+Engineering+Intern" in url and "f_JT=I" in url
    assert "cookie" not in repr(call.run_input).lower()

    row = runs_db.run(outcome.run_id)
    assert row["owner_id"] == owner and row["purpose"] == "jobs"
    assert row["status"] == "running" and row["apify_run_id"] == "run1"
    assert row["run_key"] == _key(owner) and row["actor"] == PRIMARY
    assert row["params"] == PARAMS
    assert row["max_items"] == 100 and row["max_charge_usd"] == Decimal("0.25")
    assert row["started_at"] is not None and row["poll_attempts"] == 0
    # No token anywhere in the row.
    assert TOKEN not in repr(row)

    (poll,) = runs_db.outbox(tasks.POLL_PROVIDER_RUN)
    key, run_at, args = poll
    assert key == f"listings.poll_provider_run:provider_run:{outcome.run_id}"
    assert args == {"provider_run_id": str(outcome.run_id)}
    assert run_at - row["started_at"] >= timedelta(seconds=55)
    assert runs_db.ops("listings.provider_run_started") == [(owner, outcome.run_id, {})]


@pytest.mark.parametrize(
    "case",
    ["no_consent", "paused", "exhausted", "disabled", "source_disabled", "no_key", "no_actor"],
)
async def test_refused(
    case: str,
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    listing_db: ListingDb,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await make_student(
        accounts,
        runs_db,
        provider_runs=case != "no_consent",
        key=case != "no_key",
    )
    if case == "paused":
        runs_db.execute(
            "INSERT INTO app.provider_status (owner_id, status, reason) "
            "VALUES (%s, 'paused', 'key_invalid')",
            (owner,),
        )
    if case == "exhausted":
        runs_db.execute(
            "INSERT INTO app.provider_status (owner_id, status, exhausted_until) "
            "VALUES (%s, 'active', now() + interval '3 days')",
            (owner,),
        )
    if case == "source_disabled":
        listing_db.execute("UPDATE app.sources SET enabled = false WHERE id = 'linkedin'")
    if case in ("disabled", "no_actor"):
        env = {"disabled": ("PROVIDER_RUNS_ENABLED", "false"), "no_actor": ("APIFY_ACTOR_ALLOWLIST", "")}
        monkeypatch.setenv(*env[case])
        get_settings.cache_clear()
    try:
        outcome = await _start(owner)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert outcome.status == "refused" and outcome.error_kind == case
    assert runner.started == []
    (row,) = runs_db.runs(owner)
    assert row["status"] == "refused" and row["error_kind"] == case
    assert row["apify_run_id"] is None and row["finished_at"] is not None
    assert runs_db.outbox(tasks.POLL_PROVIDER_RUN) == []
    assert runs_db.ops("listings.provider_run_refused") == [(owner, row["id"], {case: 1})]


async def test_past_exhaustion_does_not_block(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runs_db.execute(
        "INSERT INTO app.provider_status (owner_id, status, exhausted_until) "
        "VALUES (%s, 'active', now() - interval '1 minute')",
        (owner,),
    )
    assert (await _start(owner)).status == "running"


async def test_refused_row_is_checked_again_for_the_same_key(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner, listing_db: ListingDb  # noqa: F811
) -> None:
    owner = await make_student(accounts, runs_db)
    listing_db.execute("UPDATE app.sources SET enabled = false WHERE id = 'linkedin'")
    assert (await _start(owner)).error_kind == "source_disabled"
    listing_db.execute("UPDATE app.sources SET enabled = true WHERE id = 'linkedin'")
    outcome = await _start(owner)
    assert outcome.status == "running" and len(runner.started) == 1
    (row,) = runs_db.runs(owner)
    assert row["status"] == "running" and row["error_kind"] is None


async def test_same_key_twice_starts_once(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    first = await _start(owner)
    second = await _start(owner)
    assert second == service.RunOutcome(first.run_id, "running")
    assert len(runner.started) == 1 and len(runs_db.runs(owner)) == 1
    assert len(runs_db.outbox(tasks.POLL_PROVIDER_RUN)) == 1


def _insert_starting(db: RunsDb, owner: uuid.UUID | None, *, age_minutes: int) -> uuid.UUID:
    run_id = uuid.uuid7()
    db.execute(
        "INSERT INTO app.provider_runs (id, owner_id, purpose, run_key, actor, status, "
        "max_items, max_charge_usd, created_at) VALUES (%s, %s, 'jobs', %s, %s, 'starting', "
        "100, 0.25, now() - make_interval(mins => %s))",
        (run_id, owner, _key(owner) if owner else f"seed:{run_id}", PRIMARY, age_minutes),
    )
    return run_id


async def test_young_starting_row_is_another_start_in_flight(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    """Interleaving 1: the tick job and the key-save job start the same run_key; the second
    sees the first's fresh 'starting' row and does nothing."""
    owner = await make_student(accounts, runs_db)
    run_id = _insert_starting(runs_db, owner, age_minutes=2)
    assert await _start(owner) == service.RunOutcome(run_id, "starting")
    assert runner.started == []
    assert runs_db.run(run_id)["status"] == "starting"
    assert runs_db.ops("listings.provider_run_failed") == []


async def test_row_failed_during_the_start_orphans_the_new_run(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    """Interleaving 2: while our Apify call is in flight the row is failed (start_unknown by
    another caller or the reaper). Step 3 must not overwrite it: the run is aborted and
    recorded as orphaned."""
    owner = await make_student(accounts, runs_db)
    original = runner.start_run

    async def start_run(token, actor, run_input, **kw):  # noqa: ANN001, ANN202
        runs_db.execute(
            "UPDATE app.provider_runs SET status = 'failed', error_kind = 'start_unknown' "
            "WHERE owner_id = %s",
            (owner,),
        )
        return await original(token, actor, run_input, **kw)

    runner.start_run = start_run  # type: ignore[method-assign]
    outcome = await _start(owner)
    assert outcome.status == "failed" and outcome.error_kind == "start_unknown"
    (row,) = runs_db.runs(owner)
    assert row["status"] == "failed" and row["apify_run_id"] is None
    assert runner.aborted == [(TOKEN, "run1")]
    assert runs_db.ops("listings.provider_run_orphaned") == [(owner, row["id"], {})]
    assert runs_db.outbox(tasks.POLL_PROVIDER_RUN) == []


async def test_crash_after_start_is_never_restarted(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = _insert_starting(runs_db, owner, age_minutes=11)
    outcome = await _start(owner)
    assert outcome == service.RunOutcome(run_id, "failed", "start_unknown")
    assert runner.started == []
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == "start_unknown"
    # And a later call stays a no-op.
    assert (await _start(owner)).status == "failed" and runner.started == []
    assert runs_db.ops("listings.provider_run_failed") == [(owner, run_id, {"start_unknown": 1})]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ProviderKeyInvalid("x"), "key_invalid"),
        (ProviderRejected("x"), "rejected"),
        (ProviderUnavailable("x"), "unavailable"),
    ],
)
async def test_start_error_fails_the_run_without_retry(
    error: Exception, kind: str, accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.start_error = error  # type: ignore[assignment]
    outcome = await _start(owner)
    assert outcome.status == "failed" and outcome.error_kind == kind
    (row,) = runs_db.runs(owner)
    assert row["status"] == "failed" and row["error_kind"] == kind and row["apify_run_id"] is None
    assert runs_db.ops("listings.provider_run_failed") == [(owner, row["id"], {kind: 1})]
    assert runs_db.outbox(tasks.POLL_PROVIDER_RUN) == []
    # The same key never starts again.
    runner.start_error = None
    assert (await _start(owner)).status == "failed" and len(runner.started) == 1


async def test_no_transaction_is_open_while_apify_is_called(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    seen: list[str] = []
    original = runner.start_run

    async def start_run(token, actor, run_input, **kw):  # noqa: ANN001, ANN202
        # The row is committed as 'starting' before Apify is called.
        (row,) = runs_db.runs(owner)
        seen.append(row["status"])
        active = runs_db.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND state LIKE 'idle in transaction%%' AND pid <> pg_backend_pid()"
        )[0][0]
        seen.append(f"idle_in_tx={active}")
        return await original(token, actor, run_input, **kw)

    runner.start_run = start_run  # type: ignore[method-assign]
    assert (await _start(owner)).status == "running"
    assert seen == ["starting", "idle_in_tx=0"]


# --------------------------------------------------------------------------- poll


async def test_running_is_rescheduled_with_backoff(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("READY", None, None), RunState("RUNNING", 0.01, "ds1"))
    for attempt in (1, 2):
        await tasks.poll_provider_run(provider_run_id=run_id)
        assert runs_db.run(run_id)["poll_attempts"] == attempt
    polls = runs_db.outbox(tasks.POLL_PROVIDER_RUN)
    assert [p[0] for p in polls] == [
        f"listings.poll_provider_run:provider_run:{run_id}",
        f"listings.poll_provider_run:provider_run:{run_id}:1",
        f"listings.poll_provider_run:provider_run:{run_id}:2",
    ]
    gap1 = polls[1][1] - runs_db.run(run_id)["started_at"]
    assert timedelta(minutes=1, seconds=50) <= gap1 <= timedelta(minutes=3)
    assert runner.gets == [(TOKEN, "run1"), (TOKEN, "run1")]
    assert runs_db.run(run_id)["status"] == "running"


def test_poll_delay() -> None:
    assert [service.poll_delay(n).total_seconds() for n in range(1, 7)] == [
        120, 240, 480, 600, 600, 600
    ]


async def test_success_ingests_and_records(
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    listing_db: ListingDb,  # noqa: F811
) -> None:
    owner = await make_student(accounts, runs_db, pool=True)
    run_id = await _running(owner, runner)
    runner.queue(RunState("SUCCEEDED", 0.0137, "ds42"))
    runner.datasets["ds42"] = curious_dataset(8, 2)
    await tasks.poll_provider_run(provider_run_id=run_id)

    row = runs_db.run(run_id)
    assert row["status"] == "succeeded" and row["finished_at"] is not None
    assert (row["items"], row["ingested"], row["rejected"]) == (10, 8, 2)
    assert row["cost_usd"] == Decimal("0.013700") and row["dataset_id"] == "ds42"
    assert runner.fetches == [(TOKEN, "ds42", 100)]
    assert listing_db.count() == 8
    rows = listing_db.execute(
        "SELECT DISTINCT source, visibility_owner_id, collected_by FROM app.listings"
    )
    assert rows == [("linkedin", None, owner)]  # pool: the collector consented
    assert runs_db.ops("listings.provider_run_succeeded") == [
        (
            owner,
            run_id,
            {"items": 10, "ingested": 8, "rejected": 2, "duplicates": 0, "not_visible": 0},
        )
    ]
    # Descriptions (beyond search_doc lexemes) and raw items are never stored.
    dump = repr(
        listing_db.execute("SELECT to_jsonb(l) - 'search_doc' FROM app.listings l")
    ) + repr(runs_db.runs())
    assert DESCRIPTION_WORD not in dump and "Someone Private" not in dump
    # A repeat poll is a no-op.
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert len(runner.fetches) == 1


async def test_success_without_pool_consent_stays_private(
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    listing_db: ListingDb,  # noqa: F811
) -> None:
    owner = await make_student(accounts, runs_db, pool=False)
    run_id = await _running(owner, runner)
    runner.datasets["dsrun1"] = curious_dataset(3, 0)
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["ingested"] == 3
    assert listing_db.execute("SELECT DISTINCT visibility_owner_id FROM app.listings") == [
        (owner,)
    ]


@pytest.mark.parametrize(
    ("status", "kind"), [("FAILED", "failed"), ("ABORTED", "aborted"), ("TIMED-OUT", "timed_out")]
)
async def test_actor_failure_is_recorded_and_not_rerun(
    status: str, kind: str, accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState(status, 0.002, "ds1"))  # type: ignore[arg-type]
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == kind
    assert row["cost_usd"] == Decimal("0.002000")
    assert runs_db.ops("listings.provider_run_failed") == [(owner, run_id, {kind: 1})]
    assert len(runner.started) == 1 and runner.fetches == []
    assert len(runs_db.outbox(tasks.POLL_PROVIDER_RUN)) == 1  # only the first poll
    assert (await _start(owner)).status == "failed" and len(runner.started) == 1


async def test_transient_error_raises_and_leaves_the_row(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    before = runs_db.run(run_id)
    runner.get_error = ProviderUnavailable("down")  # type: ignore[assignment]
    with pytest.raises(ProviderUnavailable):
        await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id) == before


@pytest.mark.parametrize(
    ("error", "kind"), [(ProviderKeyInvalid("x"), "key_invalid"), (ProviderRejected("x"), "rejected")]
)
async def test_poll_auth_errors_fail_the_run(
    error: Exception, kind: str, accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.get_error = error  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == kind


async def test_timeout_after_three_hours(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runs_db.execute(
        "UPDATE app.provider_runs SET started_at = now() - interval '3 hours 1 minute' "
        "WHERE id = %s",
        (run_id,),
    )
    runner.queue(RunState("RUNNING", 0.2, "ds1"))
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == "timeout"
    assert len(runs_db.outbox(tasks.POLL_PROVIDER_RUN)) == 1


async def test_deleted_key_fails_the_poll(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runs_db.execute("DELETE FROM app.provider_credentials WHERE owner_id = %s", (owner,))
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["error_kind"] == "no_key" and runner.gets == []


async def test_unknown_run_is_a_noop(runner: FakeProviderRunner) -> None:
    outcome = await tasks.poll_provider_run(provider_run_id=uuid.uuid7())
    assert outcome is None and runner.gets == []


# --------------------------------------------------------------------------- deletion


async def test_account_deletion_cascades(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    await _start(owner)
    runs_db.execute(
        "INSERT INTO app.provider_status (owner_id, status) VALUES (%s, 'paused')", (owner,)
    )
    runs_db.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert runs_db.runs(owner) == []
    assert runs_db.execute(
        "SELECT count(*) FROM app.provider_status WHERE owner_id = %s", (owner,)
    ) == [(0,)]


async def test_provider_status_defaults_to_active(accounts: AccountFactory) -> None:
    from app.core.db import get_sessionmaker
    from app.modules.accounts import service as accounts_service

    owner = accounts.create()
    async with get_sessionmaker()() as session:
        status = await accounts_service.get_provider_status(session, owner)
    assert status == accounts_service.ACTIVE_PROVIDER_STATUS


# --------------------------------------------------------------------------- seed


def test_seed_refuses_without_a_token(
    monkeypatch: pytest.MonkeyPatch, runs_db: RunsDb, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import seed_pool

    monkeypatch.setenv("SEED_APIFY_TOKEN", "")
    get_settings.cache_clear()
    try:
        assert seed_pool.main(["--role", "SDE"]) == 2
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert "SEED_APIFY_TOKEN" in capsys.readouterr().err
    assert runs_db.runs() == []


async def test_seed_run_fills_the_pool(
    monkeypatch: pytest.MonkeyPatch,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    listing_db: ListingDb,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import seed_pool

    monkeypatch.setenv("SEED_APIFY_TOKEN", SEED_TOKEN)
    get_settings.cache_clear()
    monkeypatch.setattr(seed_pool.apify, "from_settings", lambda: runner)
    runner.queue(RunState("RUNNING", None, None), RunState("SUCCEEDED", 0.05, "ds9"))
    runner.datasets["ds9"] = curious_dataset(4, 1)
    args = seed_pool.parse_args(
        ["--role", "SDE Intern", "--location", "Pune", "--max-items", "20", "--poll-seconds", "1"]
    )
    try:
        assert await seed_pool.run(args) == 0
    finally:
        await dispose_engine()
        monkeypatch.undo()
        get_settings.cache_clear()
    (call,) = runner.started
    assert call.token == SEED_TOKEN and call.max_items == 20
    (row,) = runs_db.runs()
    assert row["owner_id"] is None and row["run_key"].startswith("seed:")
    assert row["status"] == "succeeded" and (row["ingested"], row["rejected"]) == (4, 1)
    assert listing_db.execute(
        "SELECT DISTINCT visibility_owner_id, collected_by FROM app.listings"
    ) == [(None, None)]
    assert runs_db.outbox(tasks.POLL_PROVIDER_RUN) == []  # the script polls, not a job
    out = capsys.readouterr().out
    assert "ingested=4" in out and "cost=0.050000 USD" in out and SEED_TOKEN not in out


async def test_token_is_only_for_the_seed(runner: FakeProviderRunner) -> None:
    with pytest.raises(ValueError):
        await service.start_provider_run(
            uuid.uuid7(), "jobs", PARAMS, run_key="x", token=SecretStr(SEED_TOKEN)
        )
    with pytest.raises(ValueError):
        await service.start_provider_run(None, "jobs", PARAMS, run_key="x")
    with pytest.raises(ValueError):
        await service.start_provider_run(None, "people", PARAMS, run_key="x", token=SecretStr("t"))


# --------------------------------------------------------------------------- review fixes


def _corrupt_key(db: RunsDb, owner: uuid.UUID) -> None:
    db.execute(
        "UPDATE app.provider_credentials SET ciphertext = set_byte(ciphertext, "
        "length(ciphertext) - 1, get_byte(ciphertext, length(ciphertext) - 1) # 1) "
        "WHERE owner_id = %s",
        (owner,),
    )


def _drop_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIALS_MASTER_KEYS", raising=False)
    get_settings.cache_clear()


class _BadInputSource:
    source = "linkedin"

    def run_input(self, actor: str, query: object) -> dict:
        raise ValueError("cannot drive this actor")

    def drafts_from_items(self, actor: str, items: list) -> tuple[list, int]:
        return [], 0


@pytest.mark.parametrize("kind", ["key_unreadable", "credentials_unavailable", "bad_input"])
async def test_start_failures_before_apify(
    kind: str,
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ports import source_adapter as source_adapter_port

    owner = await make_student(accounts, runs_db)
    if kind == "key_unreadable":
        _corrupt_key(runs_db, owner)
    elif kind == "credentials_unavailable":
        _drop_master_key(monkeypatch)
    else:
        source_adapter_port.install_provider_source(_BadInputSource())
    try:
        outcome = await _start(owner)
    finally:
        get_settings.cache_clear()
    assert outcome.status == "failed" and outcome.error_kind == kind
    (row,) = runs_db.runs(owner)
    assert row["status"] == "failed" and row["error_kind"] == kind
    assert runs_db.ops("listings.provider_run_failed") == [(owner, row["id"], {kind: 1})]
    assert runner.started == []


async def test_unexpected_start_error_fails_with_start_error(
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await make_student(accounts, runs_db)
    runner.start_error = RuntimeError(f"boom {TOKEN}")  # type: ignore[assignment]
    with caplog.at_level("DEBUG"):
        outcome = await _start(owner)
    assert outcome == service.RunOutcome(outcome.run_id, "failed", "start_error")
    assert runs_db.run(outcome.run_id)["error_kind"] == "start_error"
    assert TOKEN not in caplog.text


async def test_whitespace_role_titles_are_refused(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    outcome = await service.start_provider_run(
        owner, "jobs", {"role_titles": ["   ", "\t"]}, run_key=_key(owner)
    )
    assert outcome.status == "refused" and outcome.error_kind == "no_preferences"
    assert runner.started == []


@pytest.mark.parametrize(
    ("error", "kind"),
    [(ProviderKeyInvalid("x"), "key_invalid"), (ProviderRejected("x"), "rejected")],
)
async def test_fetch_auth_errors_fail_and_keep_the_cost(
    error: Exception,
    kind: str,
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("SUCCEEDED", 0.031, "ds1"))
    runner.fetch_error = error  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == kind
    assert row["cost_usd"] == Decimal("0.031000")


async def test_fetch_unavailable_raises_and_leaves_running(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("SUCCEEDED", 0.031, "ds1"))
    runner.fetch_error = ProviderUnavailable("down")  # type: ignore[assignment]
    with pytest.raises(ProviderUnavailable):
        await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["status"] == "running"


async def test_succeeded_without_a_dataset(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("SUCCEEDED", 0.02, None))
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    assert row["error_kind"] == "no_dataset" and row["cost_usd"] == Decimal("0.020000")
    assert runner.fetches == []


@pytest.mark.parametrize("kind", ["key_unreadable", "credentials_unavailable", "no_key"])
async def test_poll_without_a_usable_key_keeps_the_last_cost(
    kind: str,
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runner.queue(RunState("RUNNING", 0.004, None))
    await tasks.poll_provider_run(provider_run_id=run_id)  # records the cost so far
    if kind == "key_unreadable":
        _corrupt_key(runs_db, owner)
    elif kind == "credentials_unavailable":
        _drop_master_key(monkeypatch)
    else:
        runs_db.execute("DELETE FROM app.provider_credentials WHERE owner_id = %s", (owner,))
    try:
        await tasks.poll_provider_run(provider_run_id=run_id)
    finally:
        get_settings.cache_clear()
    row = runs_db.run(run_id)
    assert row["status"] == "failed" and row["error_kind"] == kind
    assert row["cost_usd"] == Decimal("0.004000")
    assert len(runner.gets) == 1


async def test_timeout_aborts_the_run(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    run_id = await _running(owner, runner)
    runs_db.execute(
        "UPDATE app.provider_runs SET started_at = now() - interval '4 hours' WHERE id = %s",
        (run_id,),
    )
    runner.queue(RunState("RUNNING", 0.2, None))
    await tasks.poll_provider_run(provider_run_id=run_id)
    assert runs_db.run(run_id)["error_kind"] == "timeout"
    assert runner.aborted == [(TOKEN, "run1")]


async def test_counts_identity(
    accounts: AccountFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
    listing_db: ListingDb,  # noqa: F811
) -> None:
    other = await make_student(accounts, runs_db, pool=False)
    other_run = await _running(other, runner)
    runner.datasets["dsrun1"] = [curious_item(1)]  # private to ``other``
    await tasks.poll_provider_run(provider_run_id=other_run)

    owner = await make_student(accounts, runs_db, pool=False)
    run_id = await _running(owner, runner)
    runner.datasets["dsrun2"] = [
        curious_item(1),  # another Student's private row: stored, not visible
        curious_item(2),
        curious_item(2),  # duplicate
        curious_item(3),
        curious_item(900, id=None),  # rejected
    ]
    await tasks.poll_provider_run(provider_run_id=run_id)
    row = runs_db.run(run_id)
    names = ("items", "ingested", "rejected", "duplicates", "not_visible")
    tallies = {k: row[k] for k in names}
    assert tallies == {
        "items": 5,
        "ingested": 2,
        "rejected": 1,
        "duplicates": 1,
        "not_visible": 1,
    }
    assert row["items"] == (
        row["ingested"] + row["rejected"] + row["duplicates"] + row["not_visible"]
    )
    assert runs_db.ops("listings.provider_run_succeeded")[-1][2] == tallies


def test_poll_retry_outlasts_the_run_timeout() -> None:
    from app.core.jobs import _registered_task

    policy = tasks.POLL_RETRY
    total = sum(policy.exponential_wait**n for n in range(1, policy.max_attempts))
    assert timedelta(seconds=total) > service.RUN_TIMEOUT
    strategy = _registered_task(tasks.POLL_PROVIDER_RUN).retry_strategy
    expected = policy.to_strategy()
    assert strategy is not None and expected is not None
    assert strategy.max_attempts == expected.max_attempts
    assert strategy.exponential_wait == expected.exponential_wait


# --------------------------------------------------------------------------- reaper


async def test_reaper_fails_stuck_runs(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    stuck_start = _insert_starting(runs_db, owner, age_minutes=16)
    fresh_start = _insert_starting(runs_db, None, age_minutes=5)

    other = await make_student(accounts, runs_db)
    stuck_run = await _running(other, runner)
    runs_db.execute(
        "UPDATE app.provider_runs SET started_at = now() - interval '4 hours 5 minutes', "
        "cost_usd = 0.07 WHERE id = %s",
        (stuck_run,),
    )
    third = await make_student(accounts, runs_db)
    live_run = await _running(third, runner)

    await tasks.reap_stale_runs()

    assert runs_db.run(stuck_start)["error_kind"] == "start_unknown"
    assert runs_db.run(fresh_start)["status"] == "starting"
    row = runs_db.run(stuck_run)
    assert row["status"] == "failed" and row["error_kind"] == "poll_exhausted"
    assert row["cost_usd"] == Decimal("0.070000")
    assert runs_db.run(live_run)["status"] == "running"
    assert runner.aborted == [(TOKEN, row["apify_run_id"])]
    failed = {(o, e, tuple(c)) for o, e, c in runs_db.ops("listings.provider_run_failed")}
    assert failed == {
        (owner, stuck_start, ("start_unknown",)),
        (other, stuck_run, ("poll_exhausted",)),
    }
    # Idempotent.
    await tasks.reap_stale_runs()
    assert len(runs_db.ops("listings.provider_run_failed")) == 2


def test_reaper_is_scheduled_hourly_by_the_migration() -> None:
    import importlib

    migration = importlib.import_module("migrations.versions.0013_provider_runs")
    assert migration.REAP_TASK == tasks.REAP_STALE_RUNS
    source = importlib.util.find_spec(migration.__name__).loader.get_source(  # type: ignore[union-attr]
        migration.__name__
    )
    assert "interval '1 hour'" in source and "INSERT INTO app.schedules" in source


def test_seed_help_and_charge_cap() -> None:
    from scripts import seed_pool

    with pytest.raises(SystemExit) as exc:
        seed_pool.parse_args(["--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit):
        seed_pool.parse_args(["--role", "x", "--max-charge-usd", "3"])
