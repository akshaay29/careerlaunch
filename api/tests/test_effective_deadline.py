"""AD-7: ``listings.service.effective_deadline(s)`` (Story 4.2). Phase 1 returns the
Listing's own deadline for Listings visible to the owner and not expired."""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.listings import service as listings_service
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

UNKNOWN_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def owners(accounts) -> tuple[uuid.UUID, uuid.UUID]:  # noqa: ANN001
    return accounts.create(), accounts.create()


async def test_effective_deadlines_cover_only_available_listings(
    engine: AsyncEngine, mdb: MaintenanceDb, owners: tuple[uuid.UUID, uuid.UUID]  # noqa: F811
) -> None:
    owner, other = owners
    dated = mdb.add("job-1", deadline="2026-12-31")
    undated = mdb.add("job-2")
    expired = mdb.add("job-3", deadline="2026-12-31", expired_days_ago=1)
    private_mine = mdb.add("job-4", deadline="2027-02-01", visibility_owner_id=owner)
    private_other = mdb.add("job-5", deadline="2027-02-01", visibility_owner_id=other)
    async with AsyncSession(engine) as session:
        found = await listings_service.effective_deadlines(
            session,
            owner,
            [dated, undated, expired, private_mine, private_other, UNKNOWN_ID, dated],
        )
        assert found == {
            dated: date(2026, 12, 31),
            undated: None,
            private_mine: date(2027, 2, 1),
        }
        assert await listings_service.effective_deadlines(session, owner, []) == {}
        assert await listings_service.effective_deadline(session, owner, dated) == date(
            2026, 12, 31
        )
        assert await listings_service.effective_deadline(session, owner, private_other) is None


async def test_effective_deadline_follows_the_listing(
    engine: AsyncEngine, mdb: MaintenanceDb, owners: tuple[uuid.UUID, uuid.UUID]  # noqa: F811
) -> None:
    owner, _ = owners
    listing = mdb.add("job-1", deadline="2026-12-31")
    mdb.execute("UPDATE app.listings SET deadline = '2027-01-15' WHERE id = %s", (listing,))
    async with AsyncSession(engine) as session:
        assert await listings_service.effective_deadline(session, owner, listing) == date(
            2027, 1, 15
        )
