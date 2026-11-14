"""FR-6, AD-3, AD-7: the single ingest pipeline (Story 2.1). One test (or more) per ingest row
of the I/O matrix."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import events, skills
from app.core.events import ListingsIngested, subscribe
from app.modules.listings import service
from app.ports.source_adapter import ListingDraft, SourceQuery
from tests.conftest import AccountFactory
from tests.fakes.source_adapter import FakeSourceAdapter
from tests.listing_fixtures import (  # noqa: F401 (fixture)
    DESCRIPTION,
    DESCRIPTION_ONLY_WORD,
    ListingDb,
    draft,
    listing_db,
)

pytestmark = pytest.mark.usefixtures("clean_jobs")


@pytest.fixture
def ingested() -> Iterator[list[ListingsIngested]]:
    """Record every ListingsIngested; the registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[ListingsIngested] = []

    async def record(session: Any, event: ListingsIngested) -> None:
        seen.append(event)

    subscribe(ListingsIngested, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


async def _ingest(engine: AsyncEngine, d, *, collected_by, contributes) -> tuple[uuid.UUID, bool]:
    async with AsyncSession(engine) as session:
        result = await service.ingest_draft(
            session, d, collected_by=collected_by, contributes_to_pool=contributes
        )
        await session.commit()
    return result


async def _collect(engine: AsyncEngine, adapter, *, collected_by, limit: int = 100) -> int:
    async with AsyncSession(engine) as session:
        count = await service.collect_and_ingest(
            session, adapter, SourceQuery(limit=limit), collected_by=collected_by
        )
        await session.commit()
    return count


# --------------------------------------------------------------------------- ingest new


async def test_ingest_new_from_consenting_collector(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    listing_id, created = await _ingest(engine, draft(1), collected_by=student, contributes=True)
    assert created is True
    assert listing_db.count() == 1
    row = listing_db.listing(listing_id)
    assert row is not None
    assert row["visibility_owner_id"] is None
    assert row["collected_by"] == student
    assert row["source"] == "linkedin" and row["source_job_id"] == "job-1"
    assert row["title"] == "Backend Intern 1" and row["company"] == "Acme Corp"
    assert row["location"] == "Bengaluru" and row["job_type"] == "internship"
    # Skills come from title + description, normalised by core.skills.
    assert set(row["skills"]) == skills.normalize(f"Backend Intern 1\n{DESCRIPTION}")
    assert {"python", "postgresql", "docker"} <= set(row["skills"])
    assert row["skills"] == skills.sorted_ids(row["skills"])
    assert row["vocab_version"] == skills.VOCAB_VERSION
    assert row["search_doc_text"]
    assert row["first_seen_at"] == row["last_seen_at"]
    assert (row["pin_count"], row["relevance"], row["expired_at"]) == (0, 0.0, None)


async def test_listings_has_no_description_or_owner_column(listing_db: ListingDb) -> None:
    cols = {
        r[0]
        for r in listing_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'app' AND table_name = 'listings'"
        )
    }
    assert "owner_id" not in cols
    assert not {c for c in cols if "desc" in c}


async def test_description_privacy(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    adapter = FakeSourceAdapter(drafts=[draft(1)])
    assert await _collect(engine, adapter, collected_by=student) == 1
    (listing_id,) = [r[0] for r in listing_db.execute("SELECT id FROM app.listings")]

    # No column other than the lexeme vector holds any description text...
    plain = listing_db.execute(
        "SELECT to_jsonb(l) - 'search_doc' FROM app.listings l WHERE id = %s", (listing_id,)
    )[0][0]
    assert DESCRIPTION_ONLY_WORD not in str(plain)
    # ...and the vector holds lexemes, never the text itself.
    row = listing_db.listing(listing_id)
    assert row is not None
    assert "distributed tracing dashboards" not in row["search_doc_text"]
    assert DESCRIPTION[:40] not in row["search_doc_text"]
    # No ops_events row mentions it either.
    ops = listing_db.execute("SELECT to_jsonb(e)::text FROM app.ops_events e")
    assert ops, "collect_and_ingest records an ops event"
    assert all(DESCRIPTION_ONLY_WORD not in r[0] for r in ops)
    # A description-only word still matches the search document.
    (matches,) = listing_db.execute(
        "SELECT search_doc @@ plainto_tsquery('english', %s) FROM app.listings WHERE id = %s",
        (DESCRIPTION_ONLY_WORD, listing_id),
    )[0]
    assert matches is True


async def test_search_doc_weights(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    """Title is weight A, company B, skill labels C, description D."""
    listing_id, _ = await _ingest(
        engine,
        draft(1, title="Platypus Engineer", company_raw="Wombat Ltd",
              description="We love Kubernetes and narwhals."),
        collected_by=None,
        contributes=True,
    )
    (a, b, c, d) = listing_db.execute(
        "SELECT ts_filter(search_doc, '{a}')::text, ts_filter(search_doc, '{b}')::text, "
        "ts_filter(search_doc, '{c}')::text, ts_filter(search_doc, '{d}')::text "
        "FROM app.listings WHERE id = %s",
        (listing_id,),
    )[0]
    assert "'platypus'" in a and "'wombat'" in b and "'kubernet'" in c and "'narwhal'" in d
    assert "'narwhal'" not in a + b + c


# --------------------------------------------------------------------------- duplicate


async def test_duplicate_refreshes_one_row(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    first_id, created = await _ingest(engine, draft(1), collected_by=student, contributes=True)
    assert created
    before = listing_db.listing(first_id)
    second_id, created = await _ingest(
        engine,
        draft(
            1,
            title="Backend Intern (Updated)",
            company_raw="Acme Corporation",
            location_raw="Remote",
            job_type="full_time",
            url="https://www.linkedin.com/jobs/view/1?refreshed=1",
            description="Now with Rust.",
        ),
        collected_by=student,
        contributes=True,
    )
    assert (second_id, created) == (first_id, False)
    assert listing_db.count() == 1
    after = listing_db.listing(first_id)
    assert before is not None and after is not None
    assert after["last_seen_at"] > before["last_seen_at"]
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["title"] == "Backend Intern (Updated)"
    assert after["company"] == "Acme Corporation"
    assert after["location"] == "Remote" and after["job_type"] == "full_time"
    assert after["url"].endswith("?refreshed=1")
    assert "rust" in after["skills"] and "python" not in after["skills"]
    assert "'rust'" in after["search_doc_text"]


# --------------------------------------------------------------------------- visibility


async def test_private_when_collector_has_no_pool_consent(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    adapter = FakeSourceAdapter(drafts=[draft(1)])
    assert await _collect(engine, adapter, collected_by=student) == 1
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (student, student)


async def test_pool_when_collector_has_pool_consent(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    listing_db.grant_pool(student)
    assert await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=student) == 1
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (None, student)


async def test_revoked_pool_consent_keeps_new_rows_private(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    listing_db.grant_pool(student)
    listing_db.execute(
        "UPDATE app.consents SET revoked_at = now(), end_reason = 'revoked' WHERE owner_id = %s",
        (student,),
    )
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=student)
    (row,) = listing_db.execute("SELECT visibility_owner_id FROM app.listings")
    assert row == (student,)


async def test_widen_when_consenting_student_collects_private_listing(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    private_owner, consenting = accounts.create(), accounts.create()
    listing_db.grant_pool(consenting)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=private_owner)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=consenting)
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (None, consenting)


async def test_never_narrows(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    consenting, private = accounts.create(), accounts.create()
    listing_db.grant_pool(consenting)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=consenting)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=private)
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (None, consenting)


async def test_private_listing_never_changes_owner(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    """Two non-consenting Students: it stays private to the first (AD-7 design note)."""
    first, second = accounts.create(), accounts.create()
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=first)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=second)
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (first, first)


async def test_system_collection_must_contribute(engine: AsyncEngine, listing_db: ListingDb) -> None:
    async with AsyncSession(engine) as session:
        with pytest.raises(ValueError):
            await service.ingest_draft(
                session, draft(1), collected_by=None, contributes_to_pool=False
            )
    assert listing_db.count() == 0


async def test_system_collection_goes_to_pool(engine: AsyncEngine, listing_db: ListingDb) -> None:
    assert await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=None) == 1
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (None, None)


# --------------------------------------------------------------------------- collect_and_ingest


async def test_collect_publishes_one_event_in_the_transaction(
    engine: AsyncEngine,
    accounts: AccountFactory,
    listing_db: ListingDb,
    ingested: list[ListingsIngested],
) -> None:
    student = accounts.create()
    listing_db.grant_pool(student)
    adapter = FakeSourceAdapter(drafts=[draft(n) for n in range(1, 21)])
    assert await _collect(engine, adapter, collected_by=student) == 20
    assert len(ingested) == 1
    (event,) = ingested
    assert event.collected_by == student
    stored = {r[0] for r in listing_db.execute("SELECT id FROM app.listings")}
    assert set(event.listing_ids) == stored and len(event.listing_ids) == 20
    ops = listing_db.execute(
        "SELECT owner_id, counts FROM app.ops_events WHERE kind = 'listings.ingested'"
    )
    assert ops == [(student, {"listings": 20, "created": 20, "rejected": 0})]


async def test_subscriber_failure_rolls_back_the_ingest(
    engine: AsyncEngine, listing_db: ListingDb, ingested: list[ListingsIngested]
) -> None:
    async def boom(session: Any, event: ListingsIngested) -> None:
        raise RuntimeError("subscriber failed")

    subscribe(ListingsIngested, boom)
    async with AsyncSession(engine) as session:
        with pytest.raises(RuntimeError):
            await service.collect_and_ingest(
                session, FakeSourceAdapter(drafts=[draft(1)]), SourceQuery(), collected_by=None
            )
        await session.rollback()
    assert listing_db.count() == 0


async def test_disabled_source_returns_zero_without_calling_adapter(
    engine: AsyncEngine, listing_db: ListingDb, ingested: list[ListingsIngested]
) -> None:
    listing_db.execute("UPDATE app.sources SET enabled = false WHERE id = 'linkedin'")
    adapter = FakeSourceAdapter(drafts=[draft(1)])
    assert await _collect(engine, adapter, collected_by=None) == 0
    assert adapter.calls == []
    assert ingested == []
    assert listing_db.count() == 0
    assert listing_db.execute(
        "SELECT counts FROM app.ops_events WHERE kind = 'listings.collect_skipped'"
    ) == [({"disabled": 1},)]


async def test_unknown_source_returns_zero(engine: AsyncEngine, listing_db: ListingDb) -> None:
    adapter = FakeSourceAdapter(source="nowhere", drafts=[draft(1, source="nowhere")])
    assert await _collect(engine, adapter, collected_by=None) == 0
    assert adapter.calls == []
    assert listing_db.execute(
        "SELECT counts FROM app.ops_events WHERE kind = 'listings.collect_skipped'"
    ) == [({"unknown_source": 1},)]


async def test_empty_collection_publishes_nothing(
    engine: AsyncEngine, listing_db: ListingDb, ingested: list[ListingsIngested]
) -> None:
    assert await _collect(engine, FakeSourceAdapter(drafts=[]), collected_by=None) == 0
    assert ingested == []


async def test_draft_for_another_source_is_refused(
    engine: AsyncEngine, listing_db: ListingDb
) -> None:
    adapter = FakeSourceAdapter(source="linkedin", drafts=[draft(1, source="manual")])
    async with AsyncSession(engine) as session:
        with pytest.raises(ValueError):
            await service.collect_and_ingest(session, adapter, SourceQuery(), collected_by=None)
        await session.rollback()
    assert listing_db.count() == 0


async def test_db_error_does_not_carry_the_description(
    engine: AsyncEngine, listing_db: ListingDb
) -> None:
    """A failing upsert raises an error whose text and chain never include the parameters."""
    listing_db.execute("ALTER TABLE app.listings ADD CONSTRAINT tmp_fail CHECK (title <> 'Boom')")
    try:
        async with AsyncSession(engine) as session:
            with pytest.raises(service.ListingIngestError) as info:
                await service.ingest_draft(
                    session, draft(1, title="Boom"), collected_by=None, contributes_to_pool=True
                )
            await session.rollback()
    finally:
        listing_db.execute("ALTER TABLE app.listings DROP CONSTRAINT tmp_fail")
    assert DESCRIPTION_ONLY_WORD not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


# --------------------------------------------------------------------------- account deleted


async def test_account_deletion_nulls_pool_rows_and_cascades_private_rows(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    student = accounts.create()
    pool_id, _ = await _ingest(engine, draft(1), collected_by=student, contributes=True)
    private_id, _ = await _ingest(engine, draft(2), collected_by=student, contributes=False)
    listing_db.execute("DELETE FROM app.accounts WHERE id = %s", (student,))
    pool_row = listing_db.listing(pool_id)
    assert pool_row is not None and pool_row["collected_by"] is None
    assert pool_row["visibility_owner_id"] is None
    assert listing_db.listing(private_id) is None


# --------------------------------------------------------------------------- seed script


async def test_seed_script_is_idempotent(listing_db: ListingDb) -> None:
    from app.core.db import dispose_engine
    from scripts import seed_demo_listings

    drafts = seed_demo_listings.demo_drafts()
    assert 25 <= len(drafts) <= 40
    try:
        for _ in range(2):
            assert await seed_demo_listings.run() == 0
    finally:
        await dispose_engine()
    assert listing_db.count() == len(drafts)
    rows = listing_db.execute(
        "SELECT DISTINCT source, visibility_owner_id, collected_by FROM app.listings"
    )
    assert rows == [("demo", None, None)]
    assert listing_db.execute("SELECT label, enabled FROM app.sources WHERE id = 'demo'") == [
        ("Demo data", True)
    ]
    assert all(d.url.startswith("https://") for d in drafts)


async def test_seed_script_reports_a_disabled_source(
    listing_db: ListingDb, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.db import dispose_engine
    from scripts import seed_demo_listings

    listing_db.execute(
        "INSERT INTO app.sources (id, label, enabled) VALUES ('demo', 'Demo data', false)"
    )
    try:
        assert await seed_demo_listings.run() == 1
    finally:
        await dispose_engine()
    assert "disabled" in capsys.readouterr().out
    assert listing_db.count() == 0


def test_seed_script_refuses_prod_without_writing(
    listing_db: ListingDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from scripts import seed_demo_listings

    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.example")
    get_settings.cache_clear()
    try:
        assert seed_demo_listings.main() == 2
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert listing_db.count() == 0
    assert listing_db.execute("SELECT id FROM app.sources WHERE id = 'demo'") == []


# --------------------------------------------------------------------------- authorised refresh


async def test_non_consenting_recollect_leaves_pool_content_unchanged(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    consenting, other = accounts.create(), accounts.create()
    listing_db.grant_pool(consenting)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=consenting)
    (listing_id,) = listing_db.execute("SELECT id FROM app.listings")[0]
    before = listing_db.listing(listing_id)
    thief = draft(
        1,
        title="Hijacked Title",
        company_raw="Other Co",
        url="https://evil.example/1",
        description="Rust only.",
        deadline=date(2030, 1, 1),
    )
    count = await _collect(engine, FakeSourceAdapter(drafts=[thief]), collected_by=other)
    assert count == 1  # a pool row is visible to them
    after = listing_db.listing(listing_id)
    assert before is not None and after is not None
    assert after["last_seen_at"] > before["last_seen_at"]
    for column in (
        "title",
        "company",
        "location",
        "url",
        "job_type",
        "posted_at",
        "deadline",
        "skills",
        "vocab_version",
        "search_doc_text",
        "collected_by",
        "visibility_owner_id",
        "expired_at",
    ):
        assert after[column] == before[column], column


async def test_recollecting_another_students_private_row_changes_nothing(
    engine: AsyncEngine,
    accounts: AccountFactory,
    listing_db: ListingDb,
    ingested: list[ListingsIngested],
) -> None:
    a, b = accounts.create(), accounts.create()
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=a)
    (listing_id,) = listing_db.execute("SELECT id FROM app.listings")[0]
    before = listing_db.listing(listing_id)
    ingested.clear()
    other = draft(1, title="Second version", company_raw="B Co")
    assert await _collect(engine, FakeSourceAdapter(drafts=[other]), collected_by=b) == 0
    assert ingested == []
    after = listing_db.listing(listing_id)
    assert before is not None and after is not None
    for column in (
        "title",
        "company",
        "url",
        "skills",
        "search_doc_text",
        "collected_by",
        "visibility_owner_id",
    ):
        assert after[column] == before[column], column
    assert listing_db.execute(
        "SELECT counts FROM app.ops_events WHERE kind = 'listings.ingested' AND owner_id = %s",
        (b,),
    ) == [({"listings": 0, "created": 0, "rejected": 0},)]


async def test_private_owner_refreshes_own_row(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    a = accounts.create()
    listing_id, _ = await _ingest(engine, draft(1), collected_by=a, contributes=False)
    await _ingest(engine, draft(1, title="Renamed"), collected_by=a, contributes=False)
    row = listing_db.listing(listing_id)
    assert row is not None and row["title"] == "Renamed" and row["visibility_owner_id"] == a


async def test_pool_listing_keeps_first_collector(
    engine: AsyncEngine, accounts: AccountFactory, listing_db: ListingDb
) -> None:
    a, b = accounts.create(), accounts.create()
    listing_db.grant_pool(a)
    listing_db.grant_pool(b)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=a)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=b)
    await _collect(engine, FakeSourceAdapter(drafts=[draft(1)]), collected_by=None)
    (row,) = listing_db.execute("SELECT visibility_owner_id, collected_by FROM app.listings")
    assert row == (None, a)


async def test_thinner_rescrape_keeps_known_values(
    engine: AsyncEngine, listing_db: ListingDb
) -> None:
    posted = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    listing_id, _ = await _ingest(
        engine,
        draft(1, posted_at=posted, deadline=date(2099, 1, 1)),
        collected_by=None,
        contributes=True,
    )
    before = listing_db.listing(listing_id)
    await _ingest(
        engine,
        draft(1, company_raw="", location_raw="", job_type=None, description=""),
        collected_by=None,
        contributes=True,
    )
    after = listing_db.listing(listing_id)
    assert before is not None and after is not None
    assert after["company"] == "Acme Corp" and after["location"] == "Bengaluru"
    assert after["job_type"] == "internship"
    assert after["posted_at"] == posted and after["deadline"] == date(2099, 1, 1)
    assert after["skills"] == before["skills"] and after["skills"]
    (desc_match, company_match) = listing_db.execute(
        "SELECT search_doc @@ plainto_tsquery('english', %s), "
        "search_doc @@ plainto_tsquery('english', 'acme') FROM app.listings WHERE id = %s",
        (DESCRIPTION_ONLY_WORD, listing_id),
    )[0]
    assert desc_match and company_match


async def test_new_values_replace_old_ones(engine: AsyncEngine, listing_db: ListingDb) -> None:
    listing_id, _ = await _ingest(engine, draft(1), collected_by=None, contributes=True)
    await _ingest(
        engine,
        draft(1, company_raw="Beta", description="Kotlin and nothing else."),
        collected_by=None,
        contributes=True,
    )
    row = listing_db.listing(listing_id)
    assert row is not None and row["company"] == "Beta" and row["skills"] == ["kotlin"]
    (old_word,) = listing_db.execute(
        "SELECT search_doc @@ plainto_tsquery('english', %s) FROM app.listings WHERE id = %s",
        (DESCRIPTION_ONLY_WORD, listing_id),
    )[0]
    assert old_word is False


@pytest.mark.parametrize(
    ("deadline", "authorised", "cleared"),
    [
        (None, True, True),
        (date(2099, 1, 1), True, True),
        (date(2000, 1, 1), True, False),
        (None, False, False),
    ],
)
async def test_reseen_listing_unexpires(
    engine: AsyncEngine,
    accounts: AccountFactory,
    listing_db: ListingDb,
    deadline: date | None,
    authorised: bool,
    cleared: bool,
) -> None:
    other = accounts.create()
    listing_id, _ = await _ingest(engine, draft(1), collected_by=None, contributes=True)
    listing_db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (listing_id,))
    await _ingest(
        engine,
        draft(1, deadline=deadline),
        collected_by=None if authorised else other,
        contributes=authorised,
    )
    row = listing_db.listing(listing_id)
    assert row is not None
    assert (row["expired_at"] is None) is cleared


# --------------------------------------------------------------------------- events and batches


async def test_ingest_draft_publishes_by_default(
    engine: AsyncEngine,
    accounts: AccountFactory,
    listing_db: ListingDb,
    ingested: list[ListingsIngested],
) -> None:
    student = accounts.create()
    listing_id, _ = await _ingest(engine, draft(1), collected_by=student, contributes=False)
    assert ingested == [ListingsIngested(listing_ids=(listing_id,), collected_by=student)]


async def test_ingest_draft_publish_false(
    engine: AsyncEngine, listing_db: ListingDb, ingested: list[ListingsIngested]
) -> None:
    async with AsyncSession(engine) as session:
        await service.ingest_draft(
            session, draft(1), collected_by=None, contributes_to_pool=True, publish=False
        )
        await session.commit()
    assert ingested == []
    assert listing_db.count() == 1


async def test_ingest_draft_no_event_for_invisible_row(
    engine: AsyncEngine,
    accounts: AccountFactory,
    listing_db: ListingDb,
    ingested: list[ListingsIngested],
) -> None:
    a, b = accounts.create(), accounts.create()
    await _ingest(engine, draft(1), collected_by=a, contributes=False)
    ingested.clear()
    await _ingest(engine, draft(1), collected_by=b, contributes=False)
    assert ingested == []


async def test_mixed_batch_skips_refused_drafts(
    engine: AsyncEngine, listing_db: ListingDb, ingested: list[ListingsIngested]
) -> None:
    listing_db.execute("ALTER TABLE app.listings ADD CONSTRAINT tmp_fail CHECK (title <> 'Boom')")
    try:
        adapter = FakeSourceAdapter(drafts=[draft(1), draft(2, title="Boom"), draft(3)])
        assert await _collect(engine, adapter, collected_by=None) == 2
    finally:
        listing_db.execute("ALTER TABLE app.listings DROP CONSTRAINT tmp_fail")
    stored = sorted(r[0] for r in listing_db.execute("SELECT source_job_id FROM app.listings"))
    assert stored == ["job-1", "job-3"]
    assert len(ingested) == 1 and len(ingested[0].listing_ids) == 2
    assert listing_db.execute(
        "SELECT counts FROM app.ops_events WHERE kind = 'listings.ingested'"
    ) == [({"listings": 2, "created": 2, "rejected": 1},)]


class _GreedySource:
    """Ignores ``query.limit`` and counts how many drafts were pulled."""

    source = "linkedin"

    def __init__(self, n: int) -> None:
        self.n = n
        self.pulled = 0

    async def collect(self, query: SourceQuery) -> AsyncIterator[ListingDraft]:
        for i in range(1, self.n + 1):
            self.pulled += 1
            yield draft(i)


async def test_collect_stops_at_the_query_limit(engine: AsyncEngine, listing_db: ListingDb) -> None:
    adapter = _GreedySource(10)
    assert await _collect(engine, adapter, collected_by=None, limit=4) == 4
    assert adapter.pulled == 4
    assert listing_db.count() == 4
