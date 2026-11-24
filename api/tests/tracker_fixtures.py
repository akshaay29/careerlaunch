"""Shared helpers for the Tracker tests (Story 4.1)."""

import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import (
    ApplicationClosed,
    ApplicationReopened,
    Event,
    clear_subscribers,
    subscribe,
)
from tests.conftest import AccountFactory, TokenFactory
from tests.maintenance_fixtures import MaintenanceDb

APPLICATIONS = "/api/v1/applications"
PROBLEM = "application/problem+json"
# Low-entropy ids for "no such row".
UNKNOWN_ID = "00000000-0000-0000-0000-000000000001"

Student = tuple[uuid.UUID, dict[str, str]]


def make_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, tokens.headers(tokens.token(account_id, email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return make_student(accounts, tokens)


@pytest.fixture
def other_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return make_student(accounts, tokens)


class TrackerDb(MaintenanceDb):
    def pin_count(self, listing_id: uuid.UUID) -> int:
        return self.execute("SELECT pin_count FROM app.listings WHERE id = %s", (listing_id,))[0][0]

    def application(self, application_id: uuid.UUID | str) -> dict | None:
        rows = self.execute(
            "SELECT status, listing_id, title, company, url, deadline, notes, applied_on, "
            "resume_id FROM app.applications WHERE id = %s",
            (application_id,),
        )
        if not rows:
            return None
        keys = (
            "status listing_id title company url deadline notes applied_on resume_id".split()
        )
        return dict(zip(keys, rows[0], strict=True))

    def history_rows(self, application_id: uuid.UUID | str) -> list[tuple]:
        return self.execute(
            "SELECT from_status, to_status FROM app.application_status_history "
            "WHERE application_id = %s ORDER BY changed_at, id",
            (application_id,),
        )

    def count_applications(self, owner_id: uuid.UUID) -> int:
        return self.execute(
            "SELECT count(*) FROM app.applications WHERE owner_id = %s", (owner_id,)
        )[0][0]

    def resume(self, owner_id: uuid.UUID) -> uuid.UUID:
        resume_id = uuid.uuid7()
        self.execute(
            "INSERT INTO app.resumes (id, owner_id, filename, content_type, size_bytes, sha256) "
            "VALUES (%s, %s, 'cv.pdf', 'application/pdf', 10, repeat('0', 64))",
            (resume_id, owner_id),
        )
        return resume_id

    def set_timezone(self, owner_id: uuid.UUID, tz: str) -> None:
        self.execute(
            "INSERT INTO app.preferences (owner_id, timezone) VALUES (%s, %s)", (owner_id, tz)
        )


@pytest.fixture
def tdb(mdb: MaintenanceDb, migrated_db: str) -> TrackerDb:
    """Listings start and end empty (``mdb``); Applications go with their accounts."""
    return TrackerDb(migrated_db)


class EventLog:
    """Records ApplicationClosed / ApplicationReopened with the Status the publisher's
    session sees at publish time (proving the event runs inside the transition's
    transaction)."""

    def __init__(self) -> None:
        self.events: list[tuple[Event, str, bool]] = []

    async def __call__(self, session: AsyncSession, event: Event) -> None:
        status = (
            await session.execute(
                text("SELECT status FROM app.applications WHERE id = :id"),
                {"id": event.application_id},  # type: ignore[attr-defined]
            )
        ).scalar_one()
        self.events.append((event, status, session.in_transaction()))

    def kinds(self) -> list[str]:
        return [type(e).__name__ for e, _, _ in self.events]


@pytest.fixture
def event_log() -> Iterator[EventLog]:
    log = EventLog()
    subscribe(ApplicationClosed, log)
    subscribe(ApplicationReopened, log)
    yield log
    clear_subscribers(ApplicationClosed)
    clear_subscribers(ApplicationReopened)


async def save(client: AsyncClient, headers: dict[str, str], listing_id: uuid.UUID) -> dict:
    resp = await client.post(APPLICATIONS, json={"listing_id": str(listing_id)}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def move(
    client: AsyncClient, headers: dict[str, str], application_id: str, status: str
):  # noqa: ANN201
    return await client.post(
        f"{APPLICATIONS}/{application_id}/status", json={"status": status}, headers=headers
    )
