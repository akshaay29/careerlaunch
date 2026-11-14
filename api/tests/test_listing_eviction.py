"""NFR-7 (Story 2.5): ``listings.evict`` (pool cap) and ``listings.quota_check`` (database
quota). One test (or more) per matching row of the I/O matrix."""

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.modules.listings import repo, service
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch):
    """Set env-backed settings for the test (cache reset before and after)."""

    def apply(**values: object) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- evict


async def test_evict_removes_the_excess_lowest_relevance_oldest_first(
    mdb: MaintenanceDb, setting
) -> None:
    """Cap 5, 8 active (2 pinned): the 3 lowest-relevance, then oldest, unpinned rows go."""
    setting(listing_pool_cap=5)
    pinned_a = mdb.add("pa", pins=1, relevance=0, seen_days_ago=90)
    pinned_b = mdb.add("pb", pins=2, relevance=0, seen_days_ago=80)
    low_old = mdb.add("low-old", relevance=1, seen_days_ago=9)
    low_new = mdb.add("low-new", relevance=1, seen_days_ago=2)
    mid = mdb.add("mid", relevance=20, seen_days_ago=30)
    high_a = mdb.add("high-a", relevance=80, seen_days_ago=50)
    high_b = mdb.add("high-b", relevance=90, seen_days_ago=60)
    zero = mdb.add("zero", relevance=0, seen_days_ago=1)

    assert await service.evict_listings() == 3

    # relevance 0 (zero), then relevance 1 oldest first (low-old, low-new).
    assert not mdb.exists(zero) and not mdb.exists(low_old) and not mdb.exists(low_new)
    for survivor in (pinned_a, pinned_b, mid, high_a, high_b):
        assert mdb.exists(survivor)
    assert mdb.ops("listings.evicted") == [{"removed": 3, "active": 8, "cap": 5}]


async def test_evict_with_everything_pinned_removes_nothing(mdb: MaintenanceDb, setting) -> None:
    setting(listing_pool_cap=2)
    ids = [mdb.add(str(n), pins=1) for n in range(4)]

    assert await service.evict_listings() == 0

    assert all(mdb.exists(i) for i in ids)
    assert mdb.ops("listings.evicted") == [{"removed": 0, "active": 4, "cap": 2}]


async def test_evict_stops_at_the_pinned_floor(mdb: MaintenanceDb, setting) -> None:
    """Cap 1 cannot be reached with two pinned rows: the unpinned one goes, no pinned one."""
    setting(listing_pool_cap=1)
    pinned = [mdb.add(str(n), pins=1) for n in range(2)]
    loose = mdb.add("loose")

    assert await service.evict_listings() == 1

    assert not mdb.exists(loose) and all(mdb.exists(i) for i in pinned)


async def test_evict_under_or_at_the_cap_removes_nothing(mdb: MaintenanceDb, setting) -> None:
    setting(listing_pool_cap=3)
    ids = [mdb.add(str(n)) for n in range(3)]

    assert await service.evict_listings() == 0

    assert all(mdb.exists(i) for i in ids)


async def test_expired_rows_do_not_count_as_active(mdb: MaintenanceDb, setting) -> None:
    setting(listing_pool_cap=2)
    live = [mdb.add(str(n)) for n in range(2)]
    expired = mdb.add("old", expired_days_ago=2)

    assert await service.evict_listings() == 0

    assert all(mdb.exists(i) for i in live) and mdb.exists(expired)


async def test_evict_is_idempotent_and_batched(mdb: MaintenanceDb, setting) -> None:
    setting(listing_pool_cap=2)
    for n in range(9):
        mdb.add(str(n), relevance=n)

    assert await service.evict_listings(batch=2) == 7
    assert await service.evict_listings(batch=2) == 0

    assert mdb.count() == 2
    # The two highest-relevance rows survive.
    assert {r[0] for r in mdb.execute("SELECT source_job_id FROM app.listings")} == {"7", "8"}


async def test_a_pin_in_flight_protects_its_row(
    engine: AsyncEngine, mdb: MaintenanceDb, setting
) -> None:
    """Evict picks candidates while ``pin`` holds one's row lock: that row is skipped (not
    waited for, not deleted)."""
    setting(listing_pool_cap=2)
    victim = mdb.add("victim", relevance=0)
    other = mdb.add("other", relevance=50)
    keep = mdb.add("keep", relevance=90)

    async with AsyncSession(engine) as pinning:
        await service.pin(pinning, victim)  # holds the row lock, uncommitted
        assert await service.evict_listings() == 1  # only `other` was free to evict
        await pinning.commit()

    assert mdb.exists(victim) and mdb.exists(keep) and not mdb.exists(other)
    assert mdb.execute("SELECT pin_count FROM app.listings WHERE id = %s", (victim,)) == [(1,)]


async def test_pinned_rows_are_never_candidates(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    """The batch statements filter pinned rows out of the candidates (the DELETE's own
    ``pin_count = 0`` re-check is a second guard that cannot be raced in a test: the candidate
    rows are locked until the DELETE runs)."""
    pinned_active = mdb.add("pinned-active", pins=1)
    loose_active = mdb.add("loose-active")
    pinned_expired = mdb.add("pinned-expired", pins=1, expired_days_ago=40)  # past any grace
    loose_expired = mdb.add("loose-expired", expired_days_ago=40)

    async with AsyncSession(engine) as session:
        assert await repo.evict_batch(session, batch=10) == 1
        await session.commit()
    assert not mdb.exists(loose_active) and mdb.exists(pinned_active)

    async with AsyncSession(engine) as session:
        assert await repo.purge_batch(session, grace_days=30, batch=10) == 1
        await session.commit()
    assert not mdb.exists(loose_expired) and mdb.exists(pinned_expired)


# --------------------------------------------------------------------------- quota


@pytest.fixture
def db_size(monkeypatch: pytest.MonkeyPatch):
    """Pretend the database is this many bytes."""

    def apply(size: int) -> None:
        async def fake(session: object) -> int:
            return size

        monkeypatch.setattr(repo, "database_size_bytes", fake)

    return apply


async def test_quota_alert_at_85_percent(
    mdb: MaintenanceDb, setting, db_size, caplog: pytest.LogCaptureFixture
) -> None:
    mib = 1024 * 1024
    setting(db_quota_bytes=500 * mib, db_quota_alert_percent=80)
    db_size(425 * mib)

    with caplog.at_level(logging.WARNING, logger="app.listings"):
        assert await service.check_quota() is True

    assert mdb.ops("listings.quota_alert") == [{"used_mb": 425, "quota_mb": 500, "percent": 85}]
    assert any(r.message == "listing_quota_alert" for r in caplog.records)


async def test_quota_fine_at_50_percent(mdb: MaintenanceDb, setting, db_size) -> None:
    mib = 1024 * 1024
    setting(db_quota_bytes=500 * mib)
    db_size(250 * mib)

    assert await service.check_quota() is False

    assert mdb.ops("listings.quota_alert") == []


async def test_quota_alerts_exactly_at_the_threshold(mdb: MaintenanceDb, setting, db_size) -> None:
    mib = 1024 * 1024
    setting(db_quota_bytes=500 * mib, db_quota_alert_percent=80)
    db_size(400 * mib - 1)
    assert await service.check_quota() is False
    db_size(400 * mib)
    assert await service.check_quota() is True


async def test_quota_reads_the_real_database_size(mdb: MaintenanceDb, setting) -> None:
    """With a one-byte quota the real ``pg_database_size`` is far above the threshold."""
    setting(db_quota_bytes=1, db_quota_alert_percent=1)

    assert await service.check_quota() is True

    (event,) = mdb.ops("listings.quota_alert")
    assert event["used_mb"] > 0 and event["quota_mb"] == 0 and event["percent"] >= 100
