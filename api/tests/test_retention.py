"""AD-17 / NFR-7: the daily ``core.purge_history`` retention job (Story 4.4). One test (or
more) per retention row of the I/O matrix."""

import asyncio
import importlib
import uuid
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from alembic import command

from app.core import jobs, retention
from app.core.config import get_settings, to_libpq_url
from tests.conftest import alembic_config

pytestmark = pytest.mark.usefixtures("clean_jobs")


class RetentionDb:
    def __init__(self, url: str) -> None:
        self.url = to_libpq_url(url)

    def execute(self, sql: str, params: tuple | dict | None = None) -> list[tuple]:
        with psycopg.connect(self.url, autocommit=True) as conn:
            # Procrastinate's triggers name their tables without a schema.
            conn.execute("SET search_path TO app")
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def job(self, status: str, days_ago: float) -> int:
        """A Procrastinate job in ``status`` whose every event is ``days_ago`` old."""
        (job_id,) = self.execute(
            "INSERT INTO app.procrastinate_jobs (queue_name, task_name, status, args) "
            "VALUES ('default', 'tests.retention', %s, '{}') RETURNING id",
            (status,),
        )[0]
        # Inserting a ``todo`` job adds a ``deferred`` event (Procrastinate's trigger).
        self.execute(
            "INSERT INTO app.procrastinate_events (job_id, type, at) "
            "VALUES (%s, 'started', now() - %s::float8 * interval '1 day')",
            (job_id, days_ago),
        )
        self.execute(
            "UPDATE app.procrastinate_events SET at = now() - %s::float8 * interval '1 day' "
            "WHERE job_id = %s",
            (days_ago, job_id),
        )
        return job_id

    def job_ids(self) -> set[int]:
        return {r[0] for r in self.execute("SELECT id FROM app.procrastinate_jobs")}

    def event_job_ids(self) -> set[int]:
        return {r[0] for r in self.execute("SELECT job_id FROM app.procrastinate_events")}

    def outbox(self, days_ago: float) -> uuid.UUID:
        row_id = uuid.uuid7()
        self.execute(
            "INSERT INTO app.job_outbox (id, task, key, created_at) "
            "VALUES (%s, 'tests.retention', %s, now() - %s::float8 * interval '1 day')",
            (row_id, f"tests.retention:outbox:{row_id}", days_ago),
        )
        return row_id

    def outbox_ids(self) -> set[uuid.UUID]:
        return {r[0] for r in self.execute("SELECT id FROM app.job_outbox")}

    def ops_event(self, days_ago: float, kind: str = "tests.retention") -> uuid.UUID:
        row_id = uuid.uuid7()
        self.execute(
            "INSERT INTO app.ops_events (id, kind, created_at) "
            "VALUES (%s, %s, now() - %s::float8 * interval '1 day')",
            (row_id, kind, days_ago),
        )
        return row_id

    def ops_ids(self, kind: str = "tests.retention") -> set[uuid.UUID]:
        return {r[0] for r in self.execute("SELECT id FROM app.ops_events WHERE kind = %s", (kind,))}

    def purge_events(self) -> list[dict]:
        rows = self.execute(
            "SELECT counts FROM app.ops_events WHERE kind = %s ORDER BY created_at, id",
            (retention.PURGE_HISTORY,),
        )
        return [r[0] for r in rows]


@pytest.fixture
def rdb(migrated_db: str) -> RetentionDb:
    return RetentionDb(migrated_db)


# --------------------------------------------------------------------------- jobs


async def test_old_finished_jobs_go_with_their_events_unfinished_and_recent_stay(
    rdb: RetentionDb,
) -> None:
    old_succeeded = rdb.job("succeeded", 8)
    old_failed = rdb.job("failed", 8)
    old_cancelled = rdb.job("cancelled", 8)
    old_aborted = rdb.job("aborted", 8)
    recent_failed = rdb.job("failed", 3)
    old_todo = rdb.job("todo", 30)
    old_doing = rdb.job("doing", 30)

    result = await retention.purge_history()

    kept = {recent_failed, old_todo, old_doing}
    assert rdb.job_ids() == kept
    assert rdb.event_job_ids() == kept  # the deleted jobs' events went with them
    assert result.jobs == 4
    assert not {old_succeeded, old_failed, old_cancelled, old_aborted} & rdb.job_ids()


async def test_a_job_with_one_recent_event_is_kept(rdb: RetentionDb) -> None:
    job = rdb.job("succeeded", 8)
    rdb.execute(
        "INSERT INTO app.procrastinate_events (job_id, type, at) "
        "VALUES (%s, 'succeeded', now() - interval '1 day')",
        (job,),
    )
    await retention.purge_history()
    assert rdb.job_ids() == {job}


# --------------------------------------------------------------------------- outbox, ops events


async def test_old_outbox_rows_are_deleted(rdb: RetentionDb) -> None:
    old = rdb.outbox(8)
    recent = rdb.outbox(1)
    result = await retention.purge_history()
    assert rdb.outbox_ids() == {recent}
    assert old not in rdb.outbox_ids()
    assert result.outbox == 1


async def test_old_ops_events_are_deleted_and_admin_audit_is_never_touched(
    rdb: RetentionDb,
) -> None:
    old = rdb.ops_event(91)
    recent = rdb.ops_event(89)
    audit_id = uuid.uuid7()
    rdb.execute(
        "INSERT INTO app.admin_audit (id, action, created_at) "
        "VALUES (%s, 'tests.retention_probe', now() - interval '2 years')",
        (audit_id,),
    )

    result = await retention.purge_history()

    assert rdb.ops_ids() == {recent}
    assert old not in rdb.ops_ids()
    assert result.ops_events == 1
    assert rdb.execute("SELECT 1 FROM app.admin_audit WHERE id = %s", (audit_id,)) == [(1,)]


# --------------------------------------------------------------------------- idempotent, batched


async def test_a_second_run_deletes_nothing_and_both_runs_are_recorded(rdb: RetentionDb) -> None:
    rdb.job("succeeded", 8)
    rdb.outbox(8)
    rdb.ops_event(91)

    first = await retention.purge_history()
    second = await retention.purge_history()

    assert first.counts() == {"jobs": 1, "outbox": 1, "ops_events": 1}
    assert second.counts() == {"jobs": 0, "outbox": 0, "ops_events": 0}
    assert rdb.purge_events() == [first.counts(), second.counts()]


async def test_2500_old_ops_events_are_deleted_in_batches_of_at_most_1000(
    rdb: RetentionDb,
) -> None:
    rdb.execute(
        "INSERT INTO app.ops_events (id, kind, created_at) "
        "SELECT gen_random_uuid(), 'tests.retention', now() - interval '100 days' "
        "FROM generate_series(1, 2500)"
    )
    keep = rdb.ops_event(1)
    result = await retention.purge_history()
    assert result.ops_events == 2500
    assert result.batches["ops_events"] == 3  # 1000 + 1000 + 500
    assert rdb.ops_ids() == {keep}


async def test_a_smaller_batch_still_clears_everything(rdb: RetentionDb) -> None:
    for _ in range(5):
        rdb.job("succeeded", 10)
    result = await retention.purge_history(batch_size=2)
    assert result.jobs == 5 and result.batches["jobs"] == 3
    assert rdb.job_ids() == set()


async def test_batch_size_over_1000_is_refused() -> None:
    with pytest.raises(ValueError):
        await retention.purge_history(batch_size=1001)


# --------------------------------------------------------------------------- config


@pytest.fixture
def short_retention(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JOB_HISTORY_RETENTION_DAYS", "2")
    monkeypatch.setenv("OPS_EVENTS_RETENTION_DAYS", "5")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.mark.usefixtures("short_retention")
async def test_the_retention_days_come_from_config(rdb: RetentionDb) -> None:
    job = rdb.job("succeeded", 3)
    ops = rdb.ops_event(6)
    result = await retention.purge_history()
    assert job not in rdb.job_ids() and ops not in rdb.ops_ids()
    assert result.jobs == 1 and result.ops_events == 1


def test_defaults_and_minimums(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    for key in ("JOB_HISTORY_RETENTION_DAYS", "OPS_EVENTS_RETENTION_DAYS"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.job_history_retention_days == 7
    assert settings.ops_events_retention_days == 90
    for field in ("job_history_retention_days", "ops_events_retention_days"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: 0})


# --------------------------------------------------------------------------- schedule


async def test_the_task_runs_the_purge(rdb: RetentionDb) -> None:
    rdb.ops_event(91)
    await retention.purge_history_task()
    assert rdb.ops_ids() == set()
    assert rdb.purge_events() == [{"jobs": 0, "outbox": 0, "ops_events": 1}]


def test_migration_names_the_task() -> None:
    migration = importlib.import_module("migrations.versions.0021_retention")
    assert migration.RETENTION_TASK == retention.PURGE_HISTORY


async def test_migration_creates_the_daily_schedule_and_the_tick_enqueues_it(
    rdb: RetentionDb,
) -> None:
    cfg = alembic_config()
    try:
        # clean_jobs emptied schedules; re-run 0021 to see what the migration inserts.
        await asyncio.to_thread(command.downgrade, cfg, "0020_manual_listings")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        rows = rdb.execute(
            "SELECT owner_id, paused, \"interval\", next_run_at > now(), "
            "next_run_at AT TIME ZONE 'UTC' FROM app.schedules WHERE kind = %s",
            (retention.PURGE_HISTORY,),
        )
        assert len(rows) == 1
        owner, paused, interval, future, slot = rows[0]
        assert owner is None and paused is False and interval == timedelta(days=1) and future
        assert (slot.hour, slot.minute) == (3, 30)

        rdb.execute(
            "UPDATE app.schedules SET next_run_at = now() - interval '1 minute' WHERE kind = %s",
            (retention.PURGE_HISTORY,),
        )
        assert await jobs.scheduler_tick() >= 1
        queued = rdb.execute(
            "SELECT key FROM app.job_outbox WHERE task = %s", (retention.PURGE_HISTORY,)
        )
        assert len(queued) == 1
        assert queued[0][0].startswith(f"{retention.PURGE_HISTORY}:schedule:")
    finally:
        # Never leave the shared database below head, whatever failed.
        await asyncio.to_thread(command.upgrade, cfg, "head")


# --------------------------------------------------------------------------- review follow-ups


async def test_a_finished_job_whose_age_is_unknown_is_kept(rdb: RetentionDb) -> None:
    # No events at all (a non-todo insert adds none).
    (no_events,) = rdb.execute(
        "INSERT INTO app.procrastinate_jobs (queue_name, task_name, status, args) "
        "VALUES ('default', 'tests.retention', 'succeeded', '{}') RETURNING id"
    )[0]
    # One old event and one with no time.
    null_time = rdb.job("failed", 30)
    rdb.execute(
        "INSERT INTO app.procrastinate_events (job_id, type, at) VALUES (%s, 'failed', NULL)",
        (null_time,),
    )
    old = rdb.job("succeeded", 30)
    result = await retention.purge_history()
    assert rdb.job_ids() == {no_events, null_time}
    assert old not in rdb.job_ids() and result.jobs == 1


async def test_dropped_outbox_rows_are_reported_before_they_go(
    rdb: RetentionDb, caplog: pytest.LogCaptureFixture
) -> None:
    rdb.outbox(8)
    rdb.outbox(10)
    rdb.execute(
        "INSERT INTO app.job_outbox (id, task, key, created_at) "
        "VALUES (%s, 'listings.collect', 'listings.collect:student:1', now() - interval '9 days')",
        (uuid.uuid7(),),
    )
    rdb.outbox(1)
    with caplog.at_level("WARNING", logger="app.retention"):
        await retention.purge_history()
    (dropped,) = [
        r[0]
        for r in rdb.execute(
            "SELECT counts FROM app.ops_events WHERE kind = %s", (retention.OUTBOX_DROPPED,)
        )
    ]
    assert dropped == {
        "tests__retention": 2,
        "listings__collect": 1,
        "total": 3,
        "oldest_age_hours": 240,
    }
    assert any(
        r.levelname == "WARNING" and r.getMessage() == "outbox_rows_dropped" for r in caplog.records
    )
    assert len(rdb.outbox_ids()) == 1


async def test_nothing_dropped_records_no_outbox_event(rdb: RetentionDb) -> None:
    rdb.outbox(1)
    await retention.purge_history()
    assert rdb.execute(
        "SELECT 1 FROM app.ops_events WHERE kind = %s", (retention.OUTBOX_DROPPED,)
    ) == []


async def test_a_failing_step_still_records_the_partial_counts(
    rdb: RetentionDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    rdb.job("succeeded", 8)
    rdb.outbox(8)
    monkeypatch.setattr(
        retention,
        "_DELETE_OPS_EVENTS",
        text("SELECT 1 / 0 WHERE CAST(:cutoff AS timestamptz) IS NOT NULL AND :batch > 0"),
    )
    with pytest.raises(Exception, match="division by zero"):
        await retention.purge_history()
    assert rdb.purge_events() == [{"jobs": 1, "outbox": 1, "ops_events": 0, "error": 1}]


def test_every_migration_schedule_kind_is_a_registered_task() -> None:
    """In a fresh interpreter (as the worker starts), importing ``app.core.jobs`` and its
    task modules registers every global schedule kind the migrations create."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    reaper = importlib.import_module("migrations.versions.0013_provider_runs")
    maintenance = importlib.import_module("migrations.versions.0015_listing_maintenance")
    retention_migration = importlib.import_module("migrations.versions.0021_retention")
    kinds = {
        reaper.REAP_TASK,
        *(t for t, _, _ in maintenance.MAINTENANCE_SCHEDULES),
        retention_migration.RETENTION_TASK,
    }
    code = (
        "import json, app.core.jobs as j; j.procrastinate_app.perform_import_paths(); "
        "print(json.dumps(sorted(j.procrastinate_app.tasks)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    registered = set(json.loads(out.stdout.strip().splitlines()[-1]))
    assert kinds <= registered, kinds - registered
