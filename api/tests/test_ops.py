"""AD-18: ops_events is append-only and holds ids and integer counts only."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_sessionmaker
from app.core.ops import record_event

pytestmark = pytest.mark.usefixtures("clean_jobs")


async def test_record_event_inserts_on_caller_session() -> None:
    owner, entity = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        event_id = await record_event(
            session,
            "resume.extracted",
            owner_id=owner,
            entity_type="resume",
            entity_id=entity,
            counts={"skills": 12},
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                text("SELECT kind, owner_id, entity_type, entity_id, counts, created_at "
                     "FROM ops_events WHERE id = :id"),
                {"id": event_id},
            )
        ).one()
    assert row.kind == "resume.extracted"
    assert (row.owner_id, row.entity_type, row.entity_id) == (owner, "resume", entity)
    assert row.counts == {"skills": 12}
    assert row.created_at is not None
    assert event_id.version == 7


async def test_record_event_does_not_commit() -> None:
    async with get_sessionmaker()() as session:
        await record_event(session, "x.test")
        await session.rollback()
    async with get_sessionmaker()() as session:
        assert (await session.execute(text("SELECT count(*) FROM ops_events"))).scalar() == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"counts": {"n": "12"}},
        {"counts": {"n": 1.5}},
        {"counts": {"n": True}},
        {"counts": {"Email": 1}},
        {"owner_id": "a@b.c"},
        {"entity_id": 42},
        {"entity_type": "Some Name"},
    ],
    ids=["str-count", "float-count", "bool-count", "bad-key", "owner-not-uuid", "entity-not-uuid",
         "entity-type-free-text"],
)
async def test_record_event_rejects_non_id_payloads(kwargs: dict) -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError):
            await record_event(session, "x.test", **kwargs)


async def test_record_event_rejects_free_text_kind() -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(ValueError):
            await record_event(session, "Alice signed up")


async def test_ops_events_block_update() -> None:
    async with get_sessionmaker()() as session:
        event_id = await record_event(session, "x.test", counts={"n": 1})
        await session.commit()
    async with get_sessionmaker()() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("UPDATE ops_events SET kind = 'x.changed' WHERE id = :id"), {"id": event_id}
            )
        await session.rollback()
    # DELETE stays possible for the 90-day retention purge (AD-17).
    async with get_sessionmaker()() as session:
        await session.execute(text("DELETE FROM ops_events WHERE id = :id"), {"id": event_id})
        await session.commit()
