"""Retention (AD-17, Story 4.4): the daily ``core.purge_history`` job.

A global ``schedules`` row (migration 0021, about 03:30 UTC) runs it once a day. It deletes:

* finished Procrastinate jobs (``succeeded``, ``failed``, ``cancelled``, ``aborted``) whose last
  event is older than ``JOB_HISTORY_RETENTION_DAYS``, with their events (``procrastinate_events``
  cascades on the job; Procrastinate's own trigger unlinks ``procrastinate_periodic_defers``);
* ``job_outbox`` rows older than ``JOB_HISTORY_RETENTION_DAYS`` (the drain flushes rows within
  minutes, so an old one is a leak). Before they go, a WARNING is logged and a
  ``core.outbox_dropped`` ops event records the rows per task and the oldest row's age;
* ``ops_events`` older than ``OPS_EVENTS_RETENTION_DAYS``.

It never touches ``todo`` or ``doing`` jobs, ``admin_audit`` or anything newer than the cutoff.
Every statement deletes at most :data:`BATCH_SIZE` rows and commits; the loop runs until a batch
comes back short, so one run clears any backlog without a long-held lock. It is idempotent: a
second run finds nothing to delete. Each run records one counts-only ``core.purge_history`` ops
event (rows deleted per table; with ``error: 1`` and the partial counts when a step fails).
A finished job is deleted only when its age is known: it has events and every event time is
set (no events or a NULL time keeps it).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import TextClause, text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.jobs import task
from app.core.ops import record_event

logger = logging.getLogger("app.retention")

PURGE_HISTORY = "core.purge_history"
OUTBOX_DROPPED = "core.outbox_dropped"
BATCH_SIZE = 1000
# Procrastinate's terminal states. ``aborting`` is legacy (unused since 3.0) and is left alone.
FINISHED_JOB_STATUSES = ("succeeded", "failed", "cancelled", "aborted")

_CUTOFFS = text(
    "SELECT now() - make_interval(days => :job_days), now() - make_interval(days => :ops_days)"
)

_DELETE_JOBS = text(
    """
    DELETE FROM procrastinate_jobs WHERE id IN (
        SELECT j.id FROM procrastinate_jobs j
        WHERE j.status::text = ANY(:statuses)
          -- Only a job whose age is known: at least one event, and every event time set
          -- and older than the cutoff. No events, or a NULL time, keeps the job.
          AND EXISTS (SELECT 1 FROM procrastinate_events e WHERE e.job_id = j.id)
          AND NOT EXISTS (
              SELECT 1 FROM procrastinate_events e
              WHERE e.job_id = j.id AND (e.at IS NULL OR e.at >= :cutoff)
          )
        ORDER BY j.id
        LIMIT :batch
    )
    """
)
_DELETE_OUTBOX = text(
    """
    DELETE FROM job_outbox WHERE id IN (
        SELECT id FROM job_outbox WHERE created_at < :cutoff ORDER BY created_at, id LIMIT :batch
    )
    """
)
# What an outbox purge is about to drop: rows per task and the oldest row's age.
_OUTBOX_LEAKS = text(
    """
    SELECT task, count(*) AS n,
           floor(extract(epoch FROM now() - min(created_at)) / 3600)::bigint AS age_hours
    FROM job_outbox WHERE created_at < :cutoff GROUP BY task ORDER BY task
    """
)
_DELETE_OPS_EVENTS = text(
    """
    DELETE FROM ops_events WHERE id IN (
        SELECT id FROM ops_events WHERE created_at < :cutoff ORDER BY created_at, id LIMIT :batch
    )
    """
)


@dataclass
class PurgeResult:
    """Rows deleted per table, and the DELETE statements run per table."""

    jobs: int = 0
    outbox: int = 0
    ops_events: int = 0
    batches: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {"jobs": self.jobs, "outbox": self.outbox, "ops_events": self.ops_events}


async def _delete_in_batches(
    statement: TextClause,
    params: dict[str, object],
    batch_size: int,
    result: PurgeResult,
    name: str,
) -> None:
    """Run ``statement`` (one batch per run) until a batch deletes fewer than ``batch_size``
    rows, committing each batch and adding it to ``result`` (so a later failure keeps the
    counts of what was already deleted)."""
    result.batches[name] = 0
    while True:
        async with get_sessionmaker()() as session:
            outcome = await session.execute(statement, {**params, "batch": batch_size})
            await session.commit()
        deleted = int(getattr(outcome, "rowcount", 0) or 0)
        setattr(result, name, getattr(result, name) + deleted)
        result.batches[name] += 1
        if deleted < batch_size:
            return


def _outbox_count_key(task_name: str) -> str:
    # ops counts keys are snake_case: ``listings.collect`` -> ``listings__collect``.
    return task_name.replace(".", "__")[:64]


async def _report_outbox_leaks(cutoff: datetime) -> None:
    """Before old outbox rows are dropped: a WARNING and one ``core.outbox_dropped`` ops event
    with the rows per task name and the oldest row's age in hours (counts only, no args)."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(_OUTBOX_LEAKS, {"cutoff": cutoff})).all()
        if not rows:
            return
        counts: dict[str, int] = {}
        for row in rows:
            key = _outbox_count_key(row.task)
            counts[key] = counts.get(key, 0) + int(row.n)
        counts["total"] = sum(int(r.n) for r in rows)
        counts["oldest_age_hours"] = max(int(r.age_hours) for r in rows)
        logger.warning("outbox_rows_dropped", extra=counts)
        await record_event(session, OUTBOX_DROPPED, counts=counts)
        await session.commit()


async def purge_history(*, batch_size: int = BATCH_SIZE) -> PurgeResult:
    """Delete old finished jobs, leaked outbox rows and old ops events (see the module
    docstring), then record one ``core.purge_history`` ops event. Returns the counts.

    If a step fails, the event is still recorded with the counts so far and ``error: 1``,
    and the error is re-raised (the jobs layer retries the task)."""
    if not 1 <= batch_size <= BATCH_SIZE:
        raise ValueError(f"batch_size must be 1..{BATCH_SIZE}")
    settings = get_settings()
    result = PurgeResult()
    try:
        async with get_sessionmaker()() as session:
            job_cutoff, ops_cutoff = (
                await session.execute(
                    _CUTOFFS,
                    {
                        "job_days": settings.job_history_retention_days,
                        "ops_days": settings.ops_events_retention_days,
                    },
                )
            ).one()
        await _delete_in_batches(
            _DELETE_JOBS,
            {"statuses": list(FINISHED_JOB_STATUSES), "cutoff": job_cutoff},
            batch_size,
            result,
            "jobs",
        )
        await _report_outbox_leaks(job_cutoff)
        await _delete_in_batches(
            _DELETE_OUTBOX, {"cutoff": job_cutoff}, batch_size, result, "outbox"
        )
        await _delete_in_batches(
            _DELETE_OPS_EVENTS, {"cutoff": ops_cutoff}, batch_size, result, "ops_events"
        )
    except Exception:
        logger.exception("purge_history_failed", extra=result.counts())
        async with get_sessionmaker()() as session:
            await record_event(session, PURGE_HISTORY, counts={**result.counts(), "error": 1})
            await session.commit()
        raise
    async with get_sessionmaker()() as session:
        await record_event(session, PURGE_HISTORY, counts=result.counts())
        await session.commit()
    logger.info("purge_history_finished", extra=result.counts())
    return result


@task(PURGE_HISTORY)
async def purge_history_task() -> None:
    await purge_history()
