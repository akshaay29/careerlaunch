"""FR-8, NFR-7 (Story 2.5): ``listings.expire`` and ``listings.purge``, plus the daily
schedules from migration 0015. One test (or more) per matching row of the I/O matrix."""

import asyncio
import importlib
from datetime import timedelta

import pytest
from alembic import command
from httpx import AsyncClient

from app.core import jobs
from app.core.config import get_settings
from app.modules.listings import service, tasks
from tests.conftest import AccountFactory, TokenFactory, alembic_config
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")

KINDS = [f"listings.{n}" for n in ("expire", "purge", "evict", "quota_check")]


# --------------------------------------------------------------------------- expire


async def test_past_deadline_expires_and_today_does_not(mdb: MaintenanceDb) -> None:
    past = mdb.add("1", deadline="2000-01-01")
    yesterday = mdb.execute("SELECT CURRENT_DATE - 1")[0][0]
    just_past = mdb.add("2", deadline=str(yesterday))
    today = mdb.add("3", deadline=str(mdb.execute("SELECT CURRENT_DATE")[0][0]))
    none = mdb.add("4")

    assert await service.expire_listings() == 2

    assert mdb.expired(past) and mdb.expired(just_past)
    assert not mdb.expired(today) and not mdb.expired(none)
    assert mdb.ops("listings.expired") == [{"deadline": 2, "unseen": 0}]


async def test_unseen_with_enough_runs_expires(mdb: MaintenanceDb) -> None:
    stale = mdb.add("1", seen_days_ago=20)
    mdb.succeeded_runs(3, hours_ago=1)

    assert await service.expire_listings() == 1

    assert mdb.expired(stale)
    assert mdb.ops("listings.expired") == [{"deadline": 0, "unseen": 1}]


async def test_unseen_with_too_few_runs_does_not_expire(mdb: MaintenanceDb) -> None:
    stale = mdb.add("1", seen_days_ago=20)
    mdb.succeeded_runs(1)

    assert await service.expire_listings() == 0
    assert not mdb.expired(stale)


async def test_runs_that_finished_before_the_last_sighting_do_not_count(
    mdb: MaintenanceDb,
) -> None:
    """The Listing was seen after two of the three runs: only one run has missed it."""
    stale = mdb.add("1", seen_days_ago=20)
    mdb.succeeded_runs(2, hours_ago=24 * 25)  # long before it was last seen
    mdb.succeeded_runs(1, hours_ago=1)

    assert await service.expire_listings() == 0
    assert not mdb.expired(stale)


async def test_failed_runs_do_not_count(mdb: MaintenanceDb) -> None:
    stale = mdb.add("1", seen_days_ago=20)
    mdb.succeeded_runs(2)
    mdb.execute(
        "INSERT INTO app.provider_runs (id, owner_id, purpose, run_key, status, "
        "max_items, max_charge_usd, finished_at, error_kind) "
        "VALUES (gen_random_uuid(), NULL, 'jobs', 'seed:failed-one', 'failed', 100, 0.25, "
        "now(), 'timeout')"
    )

    assert await service.expire_listings() == 0
    assert not mdb.expired(stale)


async def test_pinned_listings_are_not_expired(mdb: MaintenanceDb) -> None:
    """``pin`` clears ``expired_at``; the daily job must not set it again."""
    past_deadline = mdb.add("1", deadline="2000-01-01", pins=1)
    unseen = mdb.add("2", seen_days_ago=25, pins=2)
    loose = mdb.add("3", deadline="2000-01-01")
    mdb.succeeded_runs(3, hours_ago=1)

    assert await service.expire_listings() == 1

    assert not mdb.expired(past_deadline) and not mdb.expired(unseen)
    assert mdb.expired(loose)


async def test_old_runs_do_not_expire_an_idle_pool(mdb: MaintenanceDb) -> None:
    """A burst of runs, then silence: the Nth most recent run must itself be recent."""
    stale = mdb.add("1", seen_days_ago=25)
    mdb.succeeded_runs(3, hours_ago=24 * 20)

    assert await service.expire_listings() == 0
    assert not mdb.expired(stale)


async def test_recent_runs_expire_the_same_listing(mdb: MaintenanceDb) -> None:
    stale = mdb.add("1", seen_days_ago=25)
    mdb.succeeded_runs(3, hours_ago=48)

    assert await service.expire_listings() == 1
    assert mdb.expired(stale)


async def test_recently_seen_is_not_expired_even_after_many_runs(mdb: MaintenanceDb) -> None:
    fresh = mdb.add("1", seen_days_ago=3)
    mdb.succeeded_runs(5)

    assert await service.expire_listings() == 0
    assert not mdb.expired(fresh)


async def test_manual_listings_never_expire_by_unseen(mdb: MaintenanceDb) -> None:
    manual = mdb.add("1", source="manual", seen_days_ago=60)
    mdb.succeeded_runs(5)

    assert await service.expire_listings() == 0
    assert not mdb.expired(manual)


async def test_expire_is_idempotent_and_keeps_the_first_stamp(mdb: MaintenanceDb) -> None:
    listing = mdb.add("1", deadline="2000-01-01")
    await service.expire_listings()
    first = mdb.execute("SELECT expired_at FROM app.listings WHERE id = %s", (listing,))[0][0]

    assert await service.expire_listings() == 0

    again = mdb.execute("SELECT expired_at FROM app.listings WHERE id = %s", (listing,))[0][0]
    assert again == first
    assert mdb.ops("listings.expired") == [
        {"deadline": 1, "unseen": 0},
        {"deadline": 0, "unseen": 0},
    ]


async def test_expire_works_in_batches(mdb: MaintenanceDb) -> None:
    ids = [mdb.add(str(n), deadline="2000-01-01") for n in range(7)]

    assert await service.expire_listings(batch=3) == 7

    assert all(mdb.expired(i) for i in ids)
    assert mdb.ops("listings.expired") == [{"deadline": 7, "unseen": 0}]


async def test_unseen_threshold_follows_settings(
    mdb: MaintenanceDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = mdb.add("1", seen_days_ago=10)
    mdb.succeeded_runs(1)
    monkeypatch.setenv("LISTING_UNSEEN_DAYS", "7")
    monkeypatch.setenv("LISTING_UNSEEN_RUNS", "1")
    get_settings.cache_clear()
    try:
        assert await service.expire_listings() == 1
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert mdb.expired(listing)


def test_unseen_settings_have_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert (settings.listing_unseen_days, settings.listing_unseen_runs) == (14, 3)
    assert settings.listing_purge_grace_days == 30 and settings.listing_pool_cap == 30000
    assert settings.db_quota_bytes == 500 * 1024 * 1024 and settings.db_quota_alert_percent == 80
    with pytest.raises(ValidationError):
        Settings(_env_file=None, listing_unseen_days=6)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, listing_unseen_runs=0)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_quota_alert_percent=101)  # type: ignore[arg-type]


async def test_expired_rows_are_hidden_from_browse_and_detail(
    client: AsyncClient,
    tokens: TokenFactory,
    accounts: AccountFactory,
    mdb: MaintenanceDb,
) -> None:
    student = accounts.create()
    _, email, _ = accounts.row(student)  # type: ignore[misc]
    headers = tokens.headers(tokens.token(student, email))
    live = mdb.add("1")
    gone = mdb.add("2", deadline="2000-01-01")
    await service.expire_listings()

    page = await client.get("/api/v1/listings", headers=headers)
    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == [str(live)]
    assert (await client.get(f"/api/v1/listings/{live}", headers=headers)).status_code == 200
    assert (await client.get(f"/api/v1/listings/{gone}", headers=headers)).status_code == 404


# --------------------------------------------------------------------------- purge


async def test_purge_deletes_after_the_grace_period_only(mdb: MaintenanceDb) -> None:
    old = mdb.add("1", expired_days_ago=31)
    recent = mdb.add("2", expired_days_ago=10)
    live = mdb.add("3")

    assert await service.purge_listings() == 1

    assert not mdb.exists(old)
    assert mdb.exists(recent) and mdb.exists(live)
    assert mdb.ops("listings.purged") == [{"purged": 1}]


async def test_purge_never_deletes_a_pinned_listing(mdb: MaintenanceDb) -> None:
    pinned = mdb.add("1", expired_days_ago=31, pins=1)

    assert await service.purge_listings() == 0

    assert mdb.exists(pinned)


async def test_purge_is_idempotent_and_batched(mdb: MaintenanceDb) -> None:
    ids = [mdb.add(str(n), expired_days_ago=40) for n in range(5)]

    assert await service.purge_listings(batch=2) == 5
    assert await service.purge_listings(batch=2) == 0

    assert not any(mdb.exists(i) for i in ids)


async def test_purge_grace_follows_settings(
    mdb: MaintenanceDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = mdb.add("1", expired_days_ago=5)
    monkeypatch.setenv("LISTING_PURGE_GRACE_DAYS", "3")
    get_settings.cache_clear()
    try:
        assert await service.purge_listings() == 1
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert not mdb.exists(listing)


# --------------------------------------------------------------------------- tasks and schedules


async def test_tasks_run_the_jobs(mdb: MaintenanceDb) -> None:
    expiring = mdb.add("1", deadline="2000-01-01")
    purgeable = mdb.add("2", expired_days_ago=31)

    await tasks.expire()
    assert mdb.expired(expiring)
    await tasks.purge()
    assert not mdb.exists(purgeable)
    await tasks.evict()
    await tasks.quota_check()


def test_migration_names_match_the_tasks() -> None:
    migration = importlib.import_module("migrations.versions.0015_listing_maintenance")
    assert [t for t, _, _ in migration.MAINTENANCE_SCHEDULES] == [
        tasks.EXPIRE,
        tasks.PURGE,
        tasks.EVICT,
        tasks.QUOTA_CHECK,
    ]


async def test_migration_creates_four_global_daily_schedules(migrated_db: str) -> None:
    """After ``alembic upgrade head`` the four rows exist (owner NULL, one day apart, five
    minutes between jobs from about 03:00 UTC) and ``scheduler_tick`` enqueues them."""
    cfg = alembic_config()
    db = MaintenanceDb(migrated_db)
    try:
        # Alembic runs its own event loop, so it gets its own thread.
        await asyncio.to_thread(command.downgrade, cfg, "0014_spend_caps")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await _check_schedules(db)
    finally:
        # Never leave the shared database below head, whatever failed.
        await asyncio.to_thread(command.upgrade, cfg, "head")


async def _check_schedules(db: MaintenanceDb) -> None:
    rows = db.execute(
        "SELECT kind, owner_id, paused, \"interval\", next_run_at, "
        "next_run_at AT TIME ZONE 'UTC' FROM app.schedules WHERE kind = ANY(%s) "
        "ORDER BY next_run_at",
        (KINDS,),
    )
    assert [r[0] for r in rows] == [
        "listings.expire",
        "listings.purge",
        "listings.evict",
        "listings.quota_check",
    ]
    assert all(r[1] is None and r[2] is False and r[3] == timedelta(days=1) for r in rows)
    slots = [r[5] for r in rows]
    assert [s.hour for s in slots] == [3, 3, 3, 3]
    assert [s.minute for s in slots] == [0, 5, 10, 15]
    assert all(r[4] > db.execute("SELECT now() - interval '1 second'")[0][0] for r in rows)

    db.execute(
        "UPDATE app.schedules SET next_run_at = now() - interval '1 minute' "
        "WHERE kind LIKE 'listings.%'"
    )
    assert await jobs.scheduler_tick() == 4
    queued = {
        r[0]
        for r in db.execute(
            "SELECT task FROM app.job_outbox WHERE task = ANY(%s)",
            (KINDS,),
        )
    }
    assert queued == {"listings.expire", "listings.purge", "listings.evict", "listings.quota_check"}


# --------------------------------------------------------------------------- acceptance


async def test_the_daily_jobs_and_a_collection_run_together(
    client: AsyncClient,
    tokens: TokenFactory,
    accounts: AccountFactory,
    mdb: MaintenanceDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool with expired, pinned, blocked and over-cap Listings: browse shows only live
    Listings, pinned rows survive, blocked content never returns, every job is idempotent."""
    from app.core.db import get_sessionmaker
    from tests.listing_fixtures import draft

    # The ``tokens`` fixture clears the settings cache again when the test ends.
    monkeypatch.setenv("LISTING_POOL_CAP", "3")
    get_settings.cache_clear()
    student = accounts.create()
    _, email, _ = accounts.row(student)  # type: ignore[misc]
    headers = tokens.headers(tokens.token(student, email))

    best = mdb.add("best", relevance=90)
    pinned_live = mdb.add("pinned", relevance=0, pins=1)
    mid = mdb.add("mid", relevance=50)
    weak = mdb.add("weak", relevance=1)  # over the cap of 3: this one is evicted
    past_deadline = mdb.add("past", deadline="2000-01-01")
    purgeable = mdb.add("purgeable", expired_days_ago=40)
    pinned_expired = mdb.add("pinned-old", expired_days_ago=40, pins=1)
    taken_down = mdb.add("takedown", company="Umbrella Corp")
    async with get_sessionmaker()() as session:
        await service.block(session, "company", "Umbrella Corp")
        await session.commit()

    # A collection run brings a blocked draft and a new one.
    async with get_sessionmaker()() as session:
        counts = await service.ingest_drafts(
            session,
            [draft(1, company_raw="umbrella corp"), draft(2, company_raw="Globex")],
            collected_by=None,
        )
        await session.commit()
    assert (counts.listings, counts.rejected) == (1, 1)

    for _ in range(2):  # the second pass changes nothing
        await tasks.expire()
        await tasks.purge()
        await tasks.evict()
        await tasks.quota_check()

    page = await client.get("/api/v1/listings", headers=headers)
    visible = {item["id"] for item in page.json()["items"]}
    assert str(best) in visible and str(mid) in visible and str(pinned_live) in visible
    assert not visible & {str(past_deadline), str(purgeable), str(pinned_expired), str(taken_down)}
    assert mdb.exists(pinned_live) and mdb.exists(pinned_expired)  # pinned rows survive
    assert not mdb.exists(purgeable) and not mdb.exists(taken_down) and not mdb.exists(weak)
    assert not mdb.execute(
        "SELECT 1 FROM app.listings WHERE company ILIKE 'umbrella corp'"
    )
    # best, mid, pinned and the new Globex row: 4 live, cap 3, so the lowest unpinned went.
    assert mdb.execute("SELECT count(*) FROM app.listings WHERE expired_at IS NULL") == [(3,)]
