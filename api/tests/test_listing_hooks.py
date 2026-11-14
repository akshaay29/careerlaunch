"""AD-7 hooks for later epics (Story 2.5): ``pin``, ``unpin`` and ``set_relevance``. They take
the session and never commit."""

import math
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError
from app.modules.listings import service
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")


def _pin_count(mdb: MaintenanceDb, listing_id: uuid.UUID) -> int:
    return mdb.execute("SELECT pin_count FROM app.listings WHERE id = %s", (listing_id,))[0][0]


async def test_pin_twice_unpin_three_times_never_goes_below_zero(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    listing = mdb.add("1")
    async with AsyncSession(engine) as session:
        assert await service.pin(session, listing) == 1
        assert await service.pin(session, listing) == 2
        await session.commit()
    assert _pin_count(mdb, listing) == 2

    async with AsyncSession(engine) as session:
        assert await service.unpin(session, listing) == 1
        assert await service.unpin(session, listing) == 0
        assert await service.unpin(session, listing) == 0
        await session.commit()
    assert _pin_count(mdb, listing) == 0


async def test_pin_clears_expired_at(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    listing = mdb.add("1", expired_days_ago=3)
    assert mdb.expired(listing)

    async with AsyncSession(engine) as session:
        await service.pin(session, listing)
        await session.commit()

    assert not mdb.expired(listing)


async def test_unpin_does_not_touch_expiry(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    listing = mdb.add("1", pins=1, expired_days_ago=3)

    async with AsyncSession(engine) as session:
        await service.unpin(session, listing)
        await session.commit()

    assert mdb.expired(listing) and _pin_count(mdb, listing) == 0


async def test_hooks_never_commit(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    listing = mdb.add("1")
    async with AsyncSession(engine) as session:
        await service.pin(session, listing)
        await service.set_relevance(session, listing, 77.5)
        await session.rollback()

    assert _pin_count(mdb, listing) == 0
    assert mdb.execute("SELECT relevance FROM app.listings WHERE id = %s", (listing,)) == [(0.0,)]


async def test_unknown_id_raises_not_found(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    async with AsyncSession(engine) as session:
        for hook in (
            lambda: service.pin(session, uuid.uuid7()),
            lambda: service.unpin(session, uuid.uuid7()),
            lambda: service.set_relevance(session, uuid.uuid7(), 10),
        ):
            with pytest.raises(NotFoundError):
                await hook()


async def test_set_relevance_stores_and_validates(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    listing = mdb.add("1")
    async with AsyncSession(engine) as session:
        await service.set_relevance(session, listing, 42.5)
        await session.commit()
    assert mdb.execute("SELECT relevance FROM app.listings WHERE id = %s", (listing,)) == [(42.5,)]

    async with AsyncSession(engine) as session:
        await service.set_relevance(session, listing, 0)
        await service.set_relevance(session, listing, 100)
        too_big = 10**400  # an int no float can hold
        for bad in (math.nan, math.inf, -math.inf, 150, -0.5, 100.01, True, "50", None,
                    too_big, -too_big):
            with pytest.raises(ValueError):
                await service.set_relevance(session, listing, bad)  # type: ignore[arg-type]
        await session.commit()
    assert mdb.execute("SELECT relevance FROM app.listings WHERE id = %s", (listing,)) == [(100.0,)]


async def test_a_hook_waits_for_a_concurrent_lock_and_then_sees_a_deletion(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    """``pin`` takes the row lock: after a concurrent eviction deleted the row it is gone, so
    the pin raises NotFoundError instead of resurrecting it."""
    listing = mdb.add("1")
    mdb.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    async with AsyncSession(engine) as session:
        with pytest.raises(NotFoundError):
            await service.pin(session, listing)
