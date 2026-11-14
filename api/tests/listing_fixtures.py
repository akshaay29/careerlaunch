"""Shared helpers for the Listing tests (Story 2.1)."""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from app.core.config import to_libpq_url
from app.modules.accounts.consent_text import ConsentPurpose, current_text
from app.ports.source_adapter import ListingDraft

# A word that appears ONLY in the description of :func:`draft` (never in title or company).
DESCRIPTION_ONLY_WORD = "zanzibarquokka"
DESCRIPTION = (
    f"We use Python, PostgreSQL and Docker. Bonus points for {DESCRIPTION_ONLY_WORD} "
    "experience and a love of distributed tracing dashboards."
)


def draft(n: int = 1, **overrides: Any) -> ListingDraft:
    values: dict[str, Any] = {
        "source": "linkedin",
        "source_job_id": f"job-{n}",
        "url": f"https://www.linkedin.com/jobs/view/{n}",
        "title": f"Backend Intern {n}",
        "company_raw": "Acme Corp",
        "location_raw": "Bengaluru",
        "job_type": "internship",
        "description": DESCRIPTION,
        "posted_at": None,
        "deadline": None,
    }
    values.update(overrides)
    return ListingDraft(**values)


class ListingDb:
    """Direct SQL on the test database (autocommit)."""

    def __init__(self, url: str) -> None:
        self.url = to_libpq_url(url)

    def execute(self, sql: str, params: tuple | dict | None = None) -> list[tuple]:
        with psycopg.connect(self.url, autocommit=True) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def clear(self) -> None:
        self.execute("DELETE FROM app.listings")
        self.execute("DELETE FROM app.sources WHERE id NOT IN ('linkedin', 'manual')")
        self.execute("UPDATE app.sources SET enabled = true")

    def grant_pool(self, owner_id: uuid.UUID) -> None:
        text = current_text(ConsentPurpose.POOL_CONTRIBUTION)
        self.execute(
            "INSERT INTO app.consents (id, owner_id, purpose, version) VALUES (%s, %s, %s, %s)",
            (uuid.uuid7(), owner_id, text.purpose.value, text.version),
        )

    def listing(self, listing_id: uuid.UUID) -> dict[str, Any] | None:
        with psycopg.connect(self.url) as conn:
            cur = conn.execute(
                "SELECT *, search_doc::text AS search_doc_text FROM app.listings WHERE id = %s",
                (listing_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip([c.name for c in cur.description], row, strict=True))

    def count(self) -> int:
        return self.execute("SELECT count(*) FROM app.listings")[0][0]


@pytest.fixture
def listing_db(migrated_db: str) -> Iterator[ListingDb]:
    db = ListingDb(migrated_db)
    db.clear()
    yield db
    db.clear()
