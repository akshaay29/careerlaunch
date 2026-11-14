"""Shared helpers for the Listing maintenance tests (Story 2.5): Listings written straight to
the table with chosen ages, pins and relevance, and succeeded provider runs."""

import itertools
import uuid
from collections.abc import Iterator

import pytest

from tests.listing_fixtures import ListingDb

_run_ids = itertools.count(1)


class MaintenanceDb(ListingDb):
    def add(
        self,
        job_id: str,
        *,
        source: str = "linkedin",
        url: str | None = None,
        company: str = "Acme Corp",
        deadline: str | None = None,
        seen_days_ago: float = 0,
        expired_days_ago: float | None = None,
        pins: int = 0,
        relevance: float = 0,
        visibility_owner_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """One Listing row with the given age, pin count and relevance (relative to now())."""
        listing_id = uuid.uuid7()
        self.execute(
            "INSERT INTO app.listings (id, source, source_job_id, url, title, company, "
            "deadline, vocab_version, search_doc, first_seen_at, last_seen_at, expired_at, "
            "pin_count, relevance, visibility_owner_id) "
            "VALUES (%s, %s, %s, %s, 'Backend Intern', %s, %s, 'v1', to_tsvector('english', 'x'), "
            "now() - %s::float8 * interval '1 day', now() - %s::float8 * interval '1 day', "
            "CASE WHEN %s::float8 IS NULL THEN NULL "
            "ELSE now() - %s::float8 * interval '1 day' END, %s, %s, %s)",
            (
                listing_id,
                source,
                job_id,
                url or f"https://www.linkedin.com/jobs/view/{job_id}",
                company,
                deadline,
                seen_days_ago,
                seen_days_ago,
                expired_days_ago,
                expired_days_ago,
                pins,
                relevance,
                visibility_owner_id,
            ),
        )
        return listing_id

    def exists(self, listing_id: uuid.UUID) -> bool:
        return bool(self.execute("SELECT 1 FROM app.listings WHERE id = %s", (listing_id,)))

    def expired(self, listing_id: uuid.UUID) -> bool:
        rows = self.execute("SELECT expired_at FROM app.listings WHERE id = %s", (listing_id,))
        return bool(rows) and rows[0][0] is not None

    def succeeded_runs(self, count: int, *, hours_ago: float = 1) -> None:
        """``count`` succeeded linkedin runs (System seed runs) that finished ``hours_ago``."""
        for _ in range(count):
            n = next(_run_ids)
            self.execute(
                "INSERT INTO app.provider_runs (id, owner_id, purpose, run_key, actor, status, "
                "apify_run_id, max_items, max_charge_usd, finished_at) "
                "VALUES (%s, NULL, 'jobs', %s, 'curious_coder/linkedin-jobs-scraper', "
                "'succeeded', %s, 100, 0.25, now() - %s::float8 * interval '1 hour')",
                (uuid.uuid7(), f"seed:{uuid.uuid7()}", f"fakemaint{n:04d}", hours_ago),
            )

    def ops(self, kind: str) -> list[dict]:
        rows = self.execute(
            "SELECT counts FROM app.ops_events WHERE kind = %s ORDER BY created_at", (kind,)
        )
        return [r[0] for r in rows]

    def audit(self, action: str) -> list[tuple]:
        return self.execute(
            "SELECT actor_id, target_account_id FROM app.admin_audit WHERE action = %s", (action,)
        )

    def blocks(self) -> list[tuple]:
        return self.execute("SELECT kind, source, value FROM app.listing_blocks ORDER BY value")


@pytest.fixture
def mdb(migrated_db: str) -> Iterator[MaintenanceDb]:
    """Listings, takedowns and seed runs start and end empty."""
    db = MaintenanceDb(migrated_db)

    def wipe() -> None:
        db.clear()
        db.execute("DELETE FROM app.listing_blocks")
        db.execute("DELETE FROM app.provider_runs WHERE owner_id IS NULL")

    wipe()
    yield db
    wipe()
