"""AD-4: outbox enqueue, flush/dedup, scheduler_tick, retries, stalled jobs and the drain."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import text

from app.core.config import to_libpq_url
from app.core.db import get_sessionmaker
from app.core.jobs import (
    DRAIN_LOCK_KEY,
    RetryPolicy,
    drain,
    enqueue,
    flush_outbox,
    retry_stalled,
    scheduler_tick,
    task,
)
from tests.conftest import AccountFactory

pytestmark = pytest.mark.usefixtures("clean_jobs")

RUNS: list[tuple[str, dict]] = []
FLAKY_FAILURES: dict[uuid.UUID, int] = {}


@task("tests.record_run")
async def record_run(*, item_id: uuid.UUID, n: int = 0) -> None:
    RUNS.append(("record_run", {"item_id": item_id, "n": n}))


@task("tests.record_priority", queue="priority")
async def record_priority(*, item_id: uuid.UUID) -> None:
    RUNS.append(("record_priority", {"item_id": item_id}))


@task("tests.flaky", retry=RetryPolicy(max_attempts=3, exponential_wait=0))
async def flaky(*, item_id: uuid.UUID) -> None:
    left = FLAKY_FAILURES.get(item_id, 0)
    if left:
        FLAKY_FAILURES[item_id] = left - 1
        raise RuntimeError("transient")
    RUNS.append(("flaky", {"item_id": item_id}))


@task("tests.always_fails", retry=RetryPolicy(max_attempts=2, exponential_wait=0))
async def always_fails(*, item_id: uuid.UUID) -> None:
    raise RuntimeError("permanent")


@task("tests.scheduled")
async def scheduled(*, owner_id: uuid.UUID | None = None) -> None:
    RUNS.append(("scheduled", {"owner_id": owner_id}))


@task("tests.scheduled_priority", queue="priority")
async def scheduled_priority() -> None:
    RUNS.append(("scheduled_priority", {}))


@task("tests.slow")
async def slow(*, item_id: uuid.UUID) -> None:
    await asyncio.sleep(30)


@pytest.fixture(autouse=True)
def _reset_runs() -> None:
    RUNS.clear()
    FLAKY_FAILURES.clear()


def _key(name: str, item: uuid.UUID, scope: str | None = None) -> str:
    return f"{name}:item:{item}" + (f":{scope}" if scope else "")


async def _enqueue_and_commit(task_ref, key: str, **ids) -> None:
    async with get_sessionmaker()() as session:
        await enqueue(session, task_ref, key, **ids)
        await session.commit()


async def _scalar(sql: str, **params):
    async with get_sessionmaker()() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _rows(sql: str, **params):
    async with get_sessionmaker()() as session:
        return (await session.execute(text(sql), params)).all()


# --------------------------------------------------------------------------- enqueue


async def test_enqueue_then_commit_runs_once_end_to_end() -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item, n=7)
    assert await _scalar("SELECT count(*) FROM job_outbox") == 1

    result = await drain()

    assert result.status == "completed"
    assert result.deferred == 1
    assert RUNS == [("record_run", {"item_id": item, "n": 7})]
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0
    assert await _scalar("SELECT status::text FROM procrastinate_jobs") == "succeeded"
    events = await _rows("SELECT kind, entity_type, counts FROM ops_events WHERE kind LIKE 'job.%'")
    assert [(k, e) for k, e, _ in events] == [("job.succeeded", "tests.record_run")]
    assert events[0][2]["attempt"] == 1
    assert await _scalar("SELECT last_drain_at FROM drain_state WHERE id = 1") is not None
    assert await _scalar("SELECT count(*) FROM ops_events WHERE kind = 'drain.completed'") == 1

    # A second drain does not run it again.
    await drain()
    assert len(RUNS) == 1


async def test_enqueue_then_rollback_writes_nothing() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await enqueue(session, record_run, _key("tests.record_run", item), item_id=item)
        await session.rollback()
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0
    await drain()
    assert RUNS == []
    assert await _scalar("SELECT count(*) FROM procrastinate_jobs") == 0


async def test_enqueue_never_commits() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await enqueue(session, record_run, _key("tests.record_run", item), item_id=item)
        # Not visible to another transaction until the caller commits.
        assert await _scalar("SELECT count(*) FROM job_outbox") == 0
        await session.commit()
    assert await _scalar("SELECT count(*) FROM job_outbox") == 1


@pytest.mark.parametrize(
    ("key", "ids"),
    [
        ("tests.record_run:item:{item}", {"item_id": "not-a-uuid"}),
        ("tests.record_run:item:{item}", {"item_id": 1.5}),
        ("tests.record_run:item:{item}", {"item_id": True}),
        ("tests.record_run:item:{item}", {"email": "a@b.c"}),
        ("tests.record_run:item:{item}", {"item_id": None}),
        ("tests.other:item:{item}", {}),
        ("tests.record_run:Item:{item}", {}),
        ("tests.record_run:item:XYZ", {}),
        ("tests.record_run:item:{item}:bad scope", {}),
        ("tests.record_run:item", {}),
        ("tests.record_run:item:{item}:a:b", {}),
    ],
    ids=["str-arg", "float-arg", "bool-arg", "pii-arg", "none-arg", "wrong-task", "bad-entity",
         "bad-id", "bad-scope", "missing-id", "extra-part"],
)
async def test_bad_args_raise_and_write_nothing(key: str, ids: dict) -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError):
            await enqueue(session, record_run, key.format(item=item), **ids)
        await session.commit()
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0


async def test_bad_queue_and_naive_run_at_rejected() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError):
            await enqueue(session, record_run, _key("tests.record_run", item), queue="nope")
        with pytest.raises(ValueError):
            await enqueue(
                session, record_run, _key("tests.record_run", item), run_at=datetime(2030, 1, 1)
            )
        with pytest.raises(ValueError):
            await enqueue(session, "NotATask", "NotATask:item:1")
        await session.commit()
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0


async def test_unregistered_task_name_rejected() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError, match="registered"):
            await enqueue(session, "tests.not_registered", f"tests.not_registered:item:{item}")
        await session.commit()
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0


async def test_bare_name_uses_registered_queue() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await enqueue(session, "tests.record_priority", _key("tests.record_priority", item), item_id=item)
        await session.commit()
    assert await _scalar("SELECT queue FROM job_outbox") == "priority"


async def test_key_with_scope_and_int_ids_accepted() -> None:
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await enqueue(
            session, record_run, _key("tests.record_run", item, "2026-11-14"), item_id=item, n=3
        )
        await session.commit()
    (args,) = (await _rows("SELECT args FROM job_outbox"))[0]
    assert args == {"item_id": str(item), "n": 3}


def test_task_definition_is_validated() -> None:
    with pytest.raises(ValueError):
        task("NoDot")
    with pytest.raises(ValueError):
        task("tests.x", queue="other")
    with pytest.raises(TypeError):
        task("tests.sync_fn")(lambda: None)


# --------------------------------------------------------------------------- flush / dedup


async def test_duplicate_key_pending_is_one_job() -> None:
    item = uuid.uuid4()
    key = _key("tests.record_run", item)
    await _enqueue_and_commit(record_run, key, item_id=item)
    await _enqueue_and_commit(record_run, key, item_id=item)

    result = await flush_outbox()

    assert (result.deferred, result.deduplicated) == (1, 1)
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0
    assert await _scalar("SELECT count(*) FROM procrastinate_jobs") == 1
    assert await _scalar("SELECT queueing_lock FROM procrastinate_jobs") == key

    await drain()
    assert len(RUNS) == 1


async def test_outbox_row_kept_when_defer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import jobs as jobs_module

    item = uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)

    def broken(*_a, **_k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_module.procrastinate_app, "configure_task", broken)
    result = await flush_outbox()
    assert result.failed == 1
    assert await _scalar("SELECT count(*) FROM job_outbox") == 1
    monkeypatch.undo()

    result = await flush_outbox()
    assert result.deferred == 1
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0


async def test_flush_outbox_processes_multiple_batches() -> None:
    for _ in range(5):
        item = uuid.uuid4()
        await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)
    result = await flush_outbox(batch_size=2)
    assert (result.deferred, result.deduplicated, result.failed) == (5, 0, 0)
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0
    assert await _scalar("SELECT count(*) FROM procrastinate_jobs") == 5


async def test_failed_rows_counted_once_per_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import jobs as jobs_module

    for _ in range(3):
        item = uuid.uuid4()
        await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)

    def broken(*_a, **_k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_module.procrastinate_app, "configure_task", broken)
    result = await flush_outbox(batch_size=2)
    assert result.failed == 3
    assert await _scalar("SELECT count(*) FROM job_outbox") == 3


async def test_run_at_and_queue_are_carried_over() -> None:
    item = uuid.uuid4()
    later = datetime.now(UTC) + timedelta(hours=1)
    async with get_sessionmaker()() as session:
        await enqueue(session, record_run, _key("tests.record_run", item), run_at=later, item_id=item)
        await session.commit()
    await drain()
    assert RUNS == []  # not due yet
    queue, scheduled_at = (await _rows("SELECT queue_name, scheduled_at FROM procrastinate_jobs"))[0]
    assert queue == "default"
    assert abs(scheduled_at - later) < timedelta(seconds=1)


# --------------------------------------------------------------------------- drain order / retries


async def test_priority_queue_runs_before_default() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", a), item_id=a)
    await _enqueue_and_commit(record_priority, _key("tests.record_priority", b), item_id=b)
    await drain()
    assert [name for name, _ in RUNS] == ["record_priority", "record_run"]


async def test_drain_queue_subset_only_runs_those_queues() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", a), item_id=a)
    await _enqueue_and_commit(record_priority, _key("tests.record_priority", b), item_id=b)
    await drain(queues=("priority",))
    assert [name for name, _ in RUNS] == ["record_priority"]


async def test_failures_are_retried_per_policy() -> None:
    item = uuid.uuid4()
    FLAKY_FAILURES[item] = 1
    await _enqueue_and_commit(flaky, _key("tests.flaky", item), item_id=item)
    await drain()
    assert RUNS == [("flaky", {"item_id": item})]
    kinds = [k for (k,) in await _rows("SELECT kind FROM ops_events WHERE kind LIKE 'job.%' ORDER BY created_at")]
    assert kinds == ["job.failed", "job.succeeded"]


async def test_retries_stop_at_max_attempts() -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(always_fails, _key("tests.always_fails", item), item_id=item)
    await drain()
    await drain()
    assert await _scalar("SELECT status::text FROM procrastinate_jobs") == "failed"
    assert await _scalar("SELECT count(*) FROM ops_events WHERE kind = 'job.failed'") == 2


def test_default_retry_policy_is_exponential_five_attempts() -> None:
    strategy = RetryPolicy().to_strategy()
    assert strategy is not None
    # Procrastinate counts retries after the first run: 1 + 4 = 5 attempts.
    assert strategy.max_attempts == 4
    assert strategy.exponential_wait > 0
    assert RetryPolicy(max_attempts=1).to_strategy() is None


async def test_stalled_jobs_are_retried() -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)
    await flush_outbox()
    # Simulate a worker that died mid-job (host went to sleep): doing, no live worker.
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE procrastinate_jobs SET status = 'doing'"))
        await session.commit()

    assert await retry_stalled() == 1
    assert await _scalar("SELECT status::text FROM procrastinate_jobs") == "todo"
    await drain()
    assert len(RUNS) == 1


async def test_stalled_job_at_retry_limit_is_failed() -> None:
    item = uuid.uuid4()
    # always_fails allows 2 runs; this stalled run is the 2nd (attempts=1).
    await _enqueue_and_commit(always_fails, _key("tests.always_fails", item), item_id=item)
    await flush_outbox()
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE procrastinate_jobs SET status = 'doing', attempts = 1"))
        await session.commit()

    assert await retry_stalled() == 0
    assert await _scalar("SELECT status::text FROM procrastinate_jobs") == "failed"
    (entity_type, counts) = (
        await _rows("SELECT entity_type, counts FROM ops_events WHERE kind = 'job.failed'")
    )[0]
    assert entity_type == "tests.always_fails"
    assert counts["final"] == 1 and counts["attempt"] == 2


async def test_drain_timeout_is_recorded() -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(slow, _key("tests.slow", item), item_id=item)
    result = await drain(timeout=1)
    assert result.status == "timed_out"
    assert await _scalar("SELECT count(*) FROM ops_events WHERE kind = 'drain.timed_out'") == 1
    # Only completed drains count for health.
    assert await _scalar("SELECT last_drain_at FROM drain_state") is None
    # The aborted job is handed back for a later drain, not lost.
    assert await _scalar("SELECT status::text FROM procrastinate_jobs") == "todo"


async def _drain_timeouts(**kwargs) -> list[float]:
    from app.core import jobs as jobs_module

    seen: list[float] = []
    real_timeout = asyncio.timeout

    def spy(delay):
        seen.append(delay)
        return real_timeout(delay)

    jobs_module.asyncio.timeout = spy  # type: ignore[assignment]
    try:
        await drain(**kwargs)
    finally:
        jobs_module.asyncio.timeout = real_timeout  # type: ignore[assignment]
    return seen


async def test_drain_timeout_is_capped() -> None:
    # 240 s cap, grace included: the work gets 240 - 5.
    assert await _drain_timeouts(timeout=10_000) == [235]
    assert await _drain_timeouts(timeout=3) == [1]


async def test_drain_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("DRAIN_TIMEOUT_SECONDS", "30")
    get_settings.cache_clear()
    try:
        assert await _drain_timeouts() == [25]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


async def test_overlapping_drain_skips(migrated_db: str) -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)
    async with await psycopg.AsyncConnection.connect(
        to_libpq_url(migrated_db), autocommit=True
    ) as holder:
        await holder.execute("SELECT pg_advisory_lock(%s)", (DRAIN_LOCK_KEY,))
        result = await drain()
        assert result.status == "skipped"
        assert RUNS == []
        assert await _scalar("SELECT count(*) FROM job_outbox") == 1
        await holder.execute("SELECT pg_advisory_unlock(%s)", (DRAIN_LOCK_KEY,))
    assert (await drain()).status == "completed"
    assert len(RUNS) == 1


async def test_concurrent_drains_run_jobs_once() -> None:
    item = uuid.uuid4()
    await _enqueue_and_commit(record_run, _key("tests.record_run", item), item_id=item)
    results = await asyncio.gather(drain(), drain())
    assert sorted(r.status for r in results) in (["completed", "skipped"], ["completed", "completed"])
    assert len(RUNS) == 1


# --------------------------------------------------------------------------- scheduler_tick


async def _add_schedule(
    *, owner: uuid.UUID | None, due: bool, paused: bool = False, kind: str = "tests.scheduled"
) -> uuid.UUID:
    sid = uuid.uuid7()
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                'INSERT INTO schedules (id, kind, owner_id, next_run_at, paused, "interval") '
                "VALUES (:id, :kind, :owner, :next, :paused, interval '1 hour')"
            ),
            {
                "id": sid,
                "kind": kind,
                "owner": owner,
                "next": datetime.now(UTC) + (timedelta(hours=-3, minutes=-10) if due else timedelta(hours=1)),
                "paused": paused,
            },
        )
        await session.commit()
    return sid


async def test_scheduler_tick_enqueues_due_and_advances(accounts: AccountFactory) -> None:
    owner = accounts.create()
    sid = await _add_schedule(owner=owner, due=True)
    before = await _scalar("SELECT next_run_at FROM schedules WHERE id = :id", id=sid)

    assert await scheduler_tick() == 1

    (task_name, key, args) = (await _rows("SELECT task, key, args FROM job_outbox"))[0]
    assert task_name == "tests.scheduled"
    assert key.startswith(f"tests.scheduled:owner:{owner}:")
    assert args == {"owner_id": str(owner)}
    after = await _scalar("SELECT next_run_at FROM schedules WHERE id = :id", id=sid)
    # Missed slots are skipped: next run is the first slot after now, on the hour grid.
    assert after > datetime.now(UTC)
    assert after - before == timedelta(hours=4)
    # Not due any more.
    assert await scheduler_tick() == 0


async def test_paused_schedule_not_enqueued(accounts: AccountFactory) -> None:
    await _add_schedule(owner=accounts.create(), due=True, paused=True)
    await _add_schedule(owner=None, due=False)
    assert await scheduler_tick() == 0
    assert await _scalar("SELECT count(*) FROM job_outbox") == 0


async def test_drain_runs_due_schedules() -> None:
    sid = await _add_schedule(owner=None, due=True)
    result = await drain()
    assert result.scheduled == 1
    assert RUNS == [("scheduled", {"owner_id": None})]
    key = await _scalar("SELECT queueing_lock FROM procrastinate_jobs")
    assert key.startswith(f"tests.scheduled:schedule:{sid}:")


async def test_invalid_schedule_is_paused_valid_one_still_runs(accounts: AccountFactory) -> None:
    bad = await _add_schedule(owner=None, due=True, kind="tests.no_such_task")
    good = await _add_schedule(owner=accounts.create(), due=True)

    result = await drain()

    assert result.status == "completed"
    assert result.scheduled == 1
    assert [name for name, _ in RUNS] == ["scheduled"]
    rows = {r.id: r for r in await _rows("SELECT id, paused, next_run_at FROM schedules")}
    assert rows[bad].paused is True
    assert rows[good].paused is False and rows[good].next_run_at > datetime.now(UTC)
    events = await _rows("SELECT entity_type, entity_id FROM ops_events WHERE kind = 'schedule.invalid'")
    assert events == [("schedule", bad)]
    # Paused now: the next tick ignores it.
    assert await scheduler_tick() == 0


async def test_priority_scheduled_task_lands_on_priority_queue() -> None:
    await _add_schedule(owner=None, due=True, kind="tests.scheduled_priority")
    assert await scheduler_tick() == 1
    assert await _scalar("SELECT queue FROM job_outbox") == "priority"
    await drain()
    assert await _scalar("SELECT queue_name FROM procrastinate_jobs") == "priority"
    assert RUNS == [("scheduled_priority", {})]
