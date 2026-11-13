"""Durable background jobs (AD-4): transactional outbox -> Procrastinate -> drain.

* ``enqueue()`` is the ONLY way to start a job. It writes a ``job_outbox`` row on the caller's
  session and never commits, so the job exists iff the caller's state change commits.
* ``task()`` is the ONLY way to define a task. Modules never import Procrastinate
  (import-linter) and never call ``defer``.
* ``drain()`` is what ``POST /internal/jobs/drain`` runs: under a Postgres advisory lock and an
  ``asyncio.timeout`` it flushes the outbox, runs ``scheduler_tick``, retries stalled jobs and
  runs the worker with ``wait=False`` on ``priority`` and then ``default``.

Job arguments are ids only: ``uuid.UUID`` values travel as strings and are turned back into
``uuid.UUID`` before the task function runs; ``int`` values stay ints.

Delivery is AT-LEAST-ONCE: a task can run more than once for the same key (a retry, a
stalled-job re-queue, an outbox row re-deferred after a crash). Every task must be idempotent.
"""

import asyncio
import inspect
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from procrastinate import App, JobContext, PsycopgConnector, RetryStrategy
from procrastinate import exceptions as procrastinate_exceptions
from procrastinate import jobs as procrastinate_jobs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, to_libpq_url
from app.core.db import APP_SCHEMA, get_sessionmaker
from app.core.ops import record_event

logger = logging.getLogger("app.jobs")

PRIORITY_QUEUE = "priority"
DEFAULT_QUEUE = "default"
QUEUES: tuple[str, ...] = (PRIORITY_QUEUE, DEFAULT_QUEUE)
MAX_DRAIN_TIMEOUT_SECONDS = 240
WORKER_SHUTDOWN_GRACE_SECONDS = 5.0
# pg_try_advisory_lock key shared by every drain (any process, any trigger).
DRAIN_LOCK_KEY = 0x434C5F445241494E  # "CL_DRAIN"

# Every module's tasks.py is imported by the worker so it knows every task name. These are
# strings, so core still never imports app.modules (import-linter kernel contract).
TASK_MODULES = [
    f"app.modules.{m}.tasks" for m in ("accounts", "profile", "listings", "matching", "tracker")
]

_TASK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


# --------------------------------------------------------------------------- Procrastinate app


async def _configure_connection(conn: psycopg.AsyncConnection[Any]) -> None:
    """Pool ``configure`` hook: pin search_path (no libpq startup ``options``; Supavisor drops it)."""
    await conn.execute(f"SET search_path TO {APP_SCHEMA}")
    await conn.commit()


def _make_connector() -> PsycopgConnector:
    return PsycopgConnector(
        conninfo=to_libpq_url(get_settings().sqlalchemy_url),
        min_size=1,
        max_size=4,
        configure=_configure_connection,
    )


procrastinate_app = App(connector=_make_connector(), import_paths=TASK_MODULES)


def _app_is_open() -> bool:
    try:
        procrastinate_app.connector.pool  # type: ignore[attr-defined]  # noqa: B018
    except procrastinate_exceptions.AppNotOpen:
        return False
    return True


@asynccontextmanager
async def open_jobs_app() -> AsyncIterator[App]:
    """Open the Procrastinate app for the duration of the block, unless it is already open."""
    opened_here = not _app_is_open()
    if opened_here:
        await procrastinate_app.open_async()
    try:
        yield procrastinate_app
    finally:
        if opened_here:
            await procrastinate_app.close_async()


# --------------------------------------------------------------------------- tasks


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for a task. ``max_attempts`` counts every run, the first one included."""

    max_attempts: int = 5
    # Seconds; the wait before retry n is ``exponential_wait ** n`` (4, 16, 64, 256 s).
    exponential_wait: int = 4

    def to_strategy(self) -> RetryStrategy | None:
        if self.max_attempts <= 1:
            return None
        # Procrastinate's max_attempts counts retries after the first run.
        return RetryStrategy(
            max_attempts=self.max_attempts - 1, exponential_wait=self.exponential_wait
        )


DEFAULT_RETRY = RetryPolicy()


@dataclass(frozen=True)
class JobTask:
    """What ``task()`` returns: a name to pass to ``enqueue`` (no ``defer`` on purpose)."""

    name: str
    queue: str
    func: Callable[..., Awaitable[Any]]

    async def __call__(self, **ids: uuid.UUID | int) -> Any:
        """Run the task body inline (tests); does not record ops events."""
        return await self.func(**ids)


def _check_task_name(name: str) -> None:
    if not isinstance(name, str) or not _TASK_NAME_RE.fullmatch(name):
        raise ValueError(f"task name must look like '<module>.<task>', got {name!r}")


def _check_queue(queue: str) -> None:
    if queue not in QUEUES:
        raise ValueError(f"queue must be one of {QUEUES}, got {queue!r}")


def _registered_task(name: str) -> Any:
    """The registered Procrastinate task for ``name`` (task modules are imported first)."""
    procrastinate_app.perform_import_paths()
    registered = procrastinate_app.tasks.get(name)
    if registered is None:
        raise ValueError(f"no task named {name!r} is registered (define it with core.jobs.task)")
    return registered


def _decode_args(raw: dict[str, Any]) -> dict[str, uuid.UUID | int]:
    # enqueue() only admits uuid.UUID (stored as str) and int, so every str is a UUID.
    return {k: uuid.UUID(v) if isinstance(v, str) else v for k, v in raw.items()}


async def _record_job_event(
    kind: str, task_name: str, context: JobContext, ids: dict[str, uuid.UUID | int]
) -> None:
    job = context.job
    owner_id = ids.get("owner_id")
    counts = {"attempt": job.attempts + 1}
    if job.id is not None:
        counts["job_id"] = job.id
    async with get_sessionmaker()() as session:
        await record_event(
            session,
            kind,
            owner_id=owner_id if isinstance(owner_id, uuid.UUID) else None,
            entity_type=task_name,
            counts=counts,
        )
        await session.commit()


def task(
    name: str,
    *,
    queue: str = DEFAULT_QUEUE,
    retry: RetryPolicy | None = DEFAULT_RETRY,
) -> Callable[[Callable[..., Awaitable[Any]]], JobTask]:
    """Register an async task. Each run records ``job.succeeded`` or ``job.failed`` in
    ``ops_events`` in its own transaction. ``retry=None`` disables retries."""
    _check_task_name(name)
    _check_queue(queue)
    strategy = retry.to_strategy() if retry is not None else None

    def decorator(func: Callable[..., Awaitable[Any]]) -> JobTask:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"task {name!r} must be an async function")

        async def run(context: JobContext, **raw: Any) -> None:
            ids = _decode_args(raw)
            try:
                await func(**ids)
            except Exception:
                try:
                    await _record_job_event("job.failed", name, context, ids)
                except Exception:
                    logger.exception("job_event_record_failed", extra={"task": name})
                raise
            try:
                await _record_job_event("job.succeeded", name, context, ids)
            except Exception:
                # The work is done (and committed); a missing log row must not re-run it.
                logger.exception("job_event_record_failed", extra={"task": name})

        procrastinate_app.task(
            name=name, queue=queue, retry=strategy or False, pass_context=True
        )(run)
        return JobTask(name=name, queue=queue, func=func)

    return decorator


# --------------------------------------------------------------------------- enqueue (outbox)

_INSERT_OUTBOX = text(
    """
    INSERT INTO job_outbox (id, task, key, queue, run_at, args)
    VALUES (:id, :task, :key, :queue, :run_at, CAST(:args AS jsonb))
    """
)


async def enqueue(
    session: AsyncSession,
    task: JobTask | str,
    key: str,
    run_at: datetime | None = None,
    queue: str | None = None,
    **ids: uuid.UUID | int,
) -> uuid.UUID:
    """Insert a ``job_outbox`` row on ``session`` (never commits). Returns the outbox row id.

    ``key`` is ``<module>.<task>:<entity_type>:<id>[:<scope>]`` and is the Procrastinate
    queueing lock: a second job with the same key is dropped while the first is still pending.
    ``queue`` defaults to the registered task's queue. Raises ``ValueError`` and writes nothing
    on bad input, including a task name that is not registered.
    """
    name = task.name if isinstance(task, JobTask) else task
    _check_task_name(name)
    registered = _registered_task(name)
    if queue is None:
        queue = registered.queue
    _check_queue(queue)
    key_re = rf"{re.escape(name)}:[a-z_]+:[0-9a-f-]+(:[A-Za-z0-9_-]+)?"
    if not isinstance(key, str) or not re.fullmatch(key_re, key):
        raise ValueError(f"job key must match '{name}:<entity_type>:<id>[:<scope>]', got {key!r}")
    if run_at is not None and (not isinstance(run_at, datetime) or run_at.utcoffset() is None):
        raise ValueError("run_at must be a timezone-aware datetime")

    args: dict[str, str | int] = {}
    for arg, value in ids.items():
        if isinstance(value, uuid.UUID):
            args[arg] = str(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            args[arg] = value
        else:
            raise ValueError(f"job argument {arg!r} must be a uuid.UUID or int (ids only)")

    outbox_id = uuid.uuid7()
    await session.execute(
        _INSERT_OUTBOX,
        {
            "id": outbox_id,
            "task": name,
            "key": key,
            "queue": queue,
            "run_at": run_at,
            "args": json.dumps(args),
        },
    )
    return outbox_id


# --------------------------------------------------------------------------- drain steps


@dataclass
class FlushResult:
    deferred: int = 0
    deduplicated: int = 0
    failed: int = 0


_SELECT_OUTBOX = text(
    """
    SELECT id, task, key, queue, run_at, args FROM job_outbox
    WHERE NOT (id = ANY(CAST(:skip AS uuid[])))
    ORDER BY created_at, id
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)
_DELETE_OUTBOX = text("DELETE FROM job_outbox WHERE id = ANY(:ids)")


async def flush_outbox(batch_size: int = 200) -> FlushResult:
    """Move outbox rows into Procrastinate with ``queueing_lock=key``.

    A row is deleted only after its defer succeeded (or found the key already enqueued, which
    is a dedup), so delivery is at-least-once. Rows whose defer fails stay for the next drain.
    """
    result = FlushResult()
    failed_ids: list[uuid.UUID] = []  # not re-selected (or re-counted) in this flush
    async with open_jobs_app():
        while True:
            async with get_sessionmaker()() as session:
                rows = (
                    await session.execute(
                        _SELECT_OUTBOX, {"limit": batch_size, "skip": failed_ids}
                    )
                ).all()
                done: list[uuid.UUID] = []
                for row in rows:
                    try:
                        deferrer = procrastinate_app.configure_task(
                            row.task, queue=row.queue, queueing_lock=row.key, schedule_at=row.run_at
                        )
                        await deferrer.defer_async(**row.args)
                        result.deferred += 1
                    except procrastinate_exceptions.AlreadyEnqueued:
                        result.deduplicated += 1
                    except Exception:
                        result.failed += 1
                        failed_ids.append(row.id)
                        logger.exception(
                            "outbox_defer_failed", extra={"outbox_id": str(row.id), "task": row.task}
                        )
                        continue
                    done.append(row.id)
                if done:
                    await session.execute(_DELETE_OUTBOX, {"ids": done})
                await session.commit()
            if len(rows) < batch_size:
                return result


_SELECT_DUE = text(
    """
    SELECT id, kind, owner_id, next_run_at FROM schedules
    WHERE NOT paused AND next_run_at <= now()
    ORDER BY next_run_at, id
    FOR UPDATE SKIP LOCKED
    """
)
# Skip missed slots: the next run is the first slot strictly after now().
_ADVANCE = text(
    """
    UPDATE schedules
    SET next_run_at = next_run_at + "interval" * (
        floor(extract(epoch FROM now() - next_run_at) / extract(epoch FROM "interval")) + 1
    )
    WHERE id = ANY(:ids)
    """
)


_PAUSE = text("UPDATE schedules SET paused = true WHERE id = ANY(:ids)")


async def scheduler_tick() -> int:
    """Enqueue every due, unpaused ``schedules`` row and advance its ``next_run_at``, in one
    transaction. ``schedules.kind`` is the task name. A row whose kind is not a registered task
    is paused and logged as ``schedule.invalid``. Returns the number of jobs enqueued."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(_SELECT_DUE)).all()
        advanced: list[uuid.UUID] = []
        invalid: list[uuid.UUID] = []
        for row in rows:
            scope = row.next_run_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            if row.owner_id is not None:
                key = f"{row.kind}:owner:{row.owner_id}:{scope}"
                ids: dict[str, uuid.UUID | int] = {"owner_id": row.owner_id}
            else:
                key = f"{row.kind}:schedule:{row.id}:{scope}"
                ids = {}
            try:
                await enqueue(session, row.kind, key, **ids)
            except ValueError:
                # Validation happens before the insert, so nothing was written. Pause the row
                # so it does not fail again on every drain.
                invalid.append(row.id)
                logger.error("schedule_invalid", extra={"schedule_id": str(row.id)})
                continue
            advanced.append(row.id)
        if advanced:
            await session.execute(_ADVANCE, {"ids": advanced})
        if invalid:
            await session.execute(_PAUSE, {"ids": invalid})
            for schedule_id in invalid:
                await record_event(
                    session, "schedule.invalid", entity_type="schedule", entity_id=schedule_id
                )
        await session.commit()
    return len(advanced)


def _max_runs(task_name: str) -> int | None:
    """Total runs a task may get (first run included); ``None`` = unlimited."""
    procrastinate_app.perform_import_paths()
    registered = procrastinate_app.tasks.get(task_name)
    strategy = registered.retry_strategy if registered is not None else None
    if strategy is None:
        return 1
    max_retries = getattr(strategy, "max_attempts", None)
    return None if not max_retries else max_retries + 1


async def _fail_stalled(job: procrastinate_jobs.Job) -> None:
    await procrastinate_app.job_manager.finish_job(
        job, status=procrastinate_jobs.Status.FAILED, delete_job=False
    )
    owner_id = job.task_kwargs.get("owner_id")
    try:
        owner = uuid.UUID(owner_id) if isinstance(owner_id, str) else None
    except ValueError:
        owner = None
    counts = {"attempt": job.attempts + 1, "final": 1}
    if job.id is not None:
        counts["job_id"] = job.id
    async with get_sessionmaker()() as session:
        await record_event(
            session, "job.failed", owner_id=owner, entity_type=job.task_name, counts=counts
        )
        await session.commit()


async def retry_stalled() -> int:
    """Re-queue jobs left in ``doing`` by a worker that died (e.g. the host went to sleep).

    A stalled run counts as an attempt: a job that has used up its task's retry limit is
    marked failed (``job.failed`` with ``final=1``) instead of being re-queued forever.
    Returns the number of jobs re-queued.
    """
    retried = 0
    async with open_jobs_app():
        manager = procrastinate_app.job_manager
        for job in await manager.get_stalled_jobs():
            try:
                max_runs = _max_runs(job.task_name)
                if max_runs is not None and job.attempts + 1 >= max_runs:
                    await _fail_stalled(job)
                    continue
                await manager.retry_job(job)
                retried += 1
            except Exception:
                logger.exception("stalled_job_retry_failed", extra={"job_id": job.id})
    return retried


async def run_worker(queue: str) -> None:
    """Run every job that is due now on ``queue``, then return (``wait=False``)."""
    _check_queue(queue)
    async with open_jobs_app():
        await procrastinate_app.run_worker_async(
            queues=[queue],
            wait=False,
            concurrency=1,
            listen_notify=False,
            install_signal_handlers=False,
            # On drain timeout the worker is cancelled; give the running job this long to
            # finish, then abort it (a later drain retries it per its retry policy).
            shutdown_graceful_timeout=WORKER_SHUTDOWN_GRACE_SECONDS,
            delete_jobs=procrastinate_jobs.DeleteJobCondition.NEVER,
            name=f"drain-{queue}",
        )


# --------------------------------------------------------------------------- drain


@dataclass
class DrainResult:
    status: Literal["completed", "timed_out", "skipped"] = "completed"
    deferred: int = 0
    deduplicated: int = 0
    defer_failed: int = 0
    scheduled: int = 0
    stalled_retried: int = 0

    def counts(self) -> dict[str, int]:
        return {k: v for k, v in asdict(self).items() if isinstance(v, int)}

    def _add_flush(self, flush: FlushResult) -> None:
        self.deferred += flush.deferred
        self.deduplicated += flush.deduplicated
        self.defer_failed += flush.failed


_UPSERT_DRAIN_STATE = text(
    """
    INSERT INTO drain_state (id, last_drain_at) VALUES (1, now())
    ON CONFLICT (id) DO UPDATE SET last_drain_at = EXCLUDED.last_drain_at
    """
)


async def _mark_drained(result: DrainResult) -> None:
    async with get_sessionmaker()() as session:
        # Only a completed drain counts for health; a drain that keeps timing out goes stale.
        if result.status == "completed":
            await session.execute(_UPSERT_DRAIN_STATE)
        await record_event(session, f"drain.{result.status}", counts=result.counts())
        await session.commit()


async def drain(queues: Iterable[str] = QUEUES, timeout: float | None = None) -> DrainResult:
    """One drain. Overlapping drains (any process) skip instead of running twice.

    ``queues`` are worked in the given order. ``timeout`` defaults to
    ``DRAIN_TIMEOUT_SECONDS`` and is capped at 240 s, grace included: the work itself gets
    ``timeout - WORKER_SHUTDOWN_GRACE_SECONDS`` (at least 1 s) so the worker's shutdown grace
    still ends inside the cap.
    """
    queues = tuple(queues)
    for q in queues:
        _check_queue(q)
    limit = float(timeout if timeout is not None else get_settings().drain_timeout_seconds)
    limit = min(limit, MAX_DRAIN_TIMEOUT_SECONDS)
    work_limit = max(limit - WORKER_SHUTDOWN_GRACE_SECONDS, 1.0)

    # A dedicated session holds the advisory lock; closing it releases the lock even if the
    # process dies mid-drain.
    lock_conn = await psycopg.AsyncConnection.connect(
        to_libpq_url(get_settings().sqlalchemy_url), autocommit=True
    )
    try:
        cur = await lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (DRAIN_LOCK_KEY,))
        row = await cur.fetchone()
        if not row or not row[0]:
            logger.info("drain_skipped", extra={"reason": "another drain holds the lock"})
            return DrainResult(status="skipped")

        result = DrainResult()
        try:
            async with asyncio.timeout(work_limit):
                async with open_jobs_app():
                    result._add_flush(await flush_outbox())
                    result.scheduled = await scheduler_tick()
                    # Jobs enqueued by the tick go straight to Procrastinate in this drain.
                    result._add_flush(await flush_outbox())
                    result.stalled_retried = await retry_stalled()
                    for q in queues:
                        await run_worker(q)
        except TimeoutError:
            result.status = "timed_out"
            logger.warning("drain_timed_out", extra={"timeout_seconds": work_limit})

        await _mark_drained(result)
        logger.info("drain_finished", extra={"status": result.status, **result.counts()})
        return result
    finally:
        await lock_conn.close()
