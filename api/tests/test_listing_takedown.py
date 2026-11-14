"""FR-8, NFR-7 (Story 2.5): the owner's takedown (``block`` / ``unblock``), the ingest check and
the ``block_listing.py`` CLI. One test (or more) per matching row of the I/O matrix."""

import asyncio
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.modules.listings import service
from scripts import block_listing
from tests.conftest import API_DIR, AccountFactory
from tests.listing_fixtures import draft
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")


async def _block(engine: AsyncEngine, kind: str, value: str, **kwargs) -> int:
    async with AsyncSession(engine) as session:
        removed = await service.block(session, kind, value, **kwargs)
        await session.commit()
    return removed


async def _unblock(engine: AsyncEngine, kind: str, value: str, **kwargs) -> bool:
    async with AsyncSession(engine) as session:
        lifted = await service.unblock(session, kind, value, **kwargs)
        await session.commit()
    return lifted


async def _ingest_many(engine: AsyncEngine, drafts) -> service.IngestCounts:
    async with AsyncSession(engine) as session:
        counts = await service.ingest_drafts(session, drafts, collected_by=None)
        await session.commit()
    return counts


# --------------------------------------------------------------------------- block


async def test_block_company_deletes_any_case_and_audits(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    gone = [
        mdb.add("1", company="Acme Corp"),
        mdb.add("2", company="ACME CORP"),
        mdb.add("3", company="  acme corp "),
    ]
    other = mdb.add("4", company="Acme Corporation")
    audits = len(mdb.audit("listings.block"))

    assert await _block(engine, "company", "  Acme Corp") == 3

    assert not any(mdb.exists(i) for i in gone) and mdb.exists(other)
    assert mdb.blocks() == [("company", None, "acme corp")]
    assert len(mdb.audit("listings.block")) == audits + 1
    assert mdb.audit("listings.block")[-1] == (None, None)
    assert mdb.ops("listings.blocked") == [{"removed": 3, "created": 1}]


async def test_block_company_is_case_folded(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    straße = mdb.add("1", company="Straße GmbH")
    assert await _block(engine, "company", "STRASSE gmbh") == 1
    assert not mdb.exists(straße)


async def test_block_url_ignores_query_fragment_and_host_case(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    target = mdb.add("1", url="https://www.linkedin.com/jobs/view/111")
    mixed = mdb.add("2", url="https://WWW.LinkedIn.com/jobs/view/222/?trk=a#top")
    keep = mdb.add("3", url="https://www.linkedin.com/jobs/view/333")

    removed = await _block(
        engine, "url", "HTTPS://www.LINKEDIN.com/jobs/view/111?refId=abc&trk=public#x"
    )
    assert removed == 1
    assert await _block(engine, "url", "https://www.linkedin.com/jobs/view/222") == 1

    assert not mdb.exists(target) and not mdb.exists(mixed) and mdb.exists(keep)
    assert mdb.blocks() == [
        ("url", None, "https://www.linkedin.com/jobs/view/111"),
        ("url", None, "https://www.linkedin.com/jobs/view/222"),
    ]


async def test_block_one_listing(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    target = mdb.add("123")
    same_id_other_source = mdb.add("123", source="manual")
    other = mdb.add("124")

    assert await _block(engine, "listing", "linkedin:123") == 1

    assert not mdb.exists(target)
    assert mdb.exists(same_id_other_source) and mdb.exists(other)
    assert mdb.blocks() == [("listing", "linkedin", "123")]


async def test_a_takedown_beats_a_pin_and_reaches_private_rows(
    engine: AsyncEngine, mdb: MaintenanceDb, accounts: AccountFactory
) -> None:
    pinned = mdb.add("1", company="Acme Corp", pins=3)
    private = mdb.add("2", company="Acme Corp", visibility_owner_id=accounts.create())
    expired = mdb.add("3", company="Acme Corp", expired_days_ago=1)

    assert await _block(engine, "company", "Acme Corp") == 3

    assert not any(mdb.exists(i) for i in (pinned, private, expired))


async def test_actor_is_recorded_in_the_audit_row(
    engine: AsyncEngine, mdb: MaintenanceDb, accounts: AccountFactory
) -> None:
    owner = accounts.create(role="admin")
    before = len(mdb.audit("listings.block"))

    await _block(engine, "company", "Initech", actor_id=owner)

    assert mdb.audit("listings.block")[before:] == [(owner, None)]


async def test_blocking_twice_is_safe(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    mdb.add("1", company="Acme Corp")
    assert await _block(engine, "company", "Acme Corp") == 1
    mdb.add("2", company="Acme Corp")  # stored before the second call (e.g. restored by hand)
    assert await _block(engine, "company", "acme corp") == 1

    assert mdb.blocks() == [("company", None, "acme corp")]
    assert mdb.ops("listings.blocked") == [
        {"removed": 1, "created": 1},
        {"removed": 1, "created": 0},
    ]


async def test_block_does_not_commit(engine: AsyncEngine, mdb: MaintenanceDb) -> None:
    listing = mdb.add("1", company="Acme Corp")
    audits = len(mdb.audit("listings.block"))
    async with AsyncSession(engine) as session:
        assert await service.block(session, "company", "Acme Corp") == 1
        await session.rollback()

    assert mdb.exists(listing) and mdb.blocks() == []
    assert len(mdb.audit("listings.block")) == audits


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("nope", "x"),
        ("company", "   "),
        ("company", ""),
        ("listing", "123"),
        ("listing", "linkedin:"),
        ("listing", "Bad Source:1"),
        ("url", "not a url"),
        ("url", "ftp://example.com/x"),
        ("url", "https://example.test/" + "a" * 2100),
        ("company", "a" * 2100),
        ("listing", "linkedin:" + "1" * 2100),
    ],
)
async def test_bad_input_is_a_value_error(
    engine: AsyncEngine, mdb: MaintenanceDb, kind: str, value: str
) -> None:
    async with AsyncSession(engine) as session:
        with pytest.raises(ValueError):
            await service.block(session, kind, value)
    assert mdb.blocks() == []


def test_canonical_url_drops_only_the_default_port() -> None:
    assert service.canonical_url("https://Example.com:443/a/?x=1#y") == "https://example.com/a"
    assert service.canonical_url("http://example.com:80/a") == "http://example.com/a"
    assert service.canonical_url("https://example.com:8443/a") == "https://example.com:8443/a"
    assert service.canonical_url("http://example.com:443/a") == "http://example.com:443/a"
    assert service.canonical_url("https://example.com:80/a") == "https://example.com:80/a"


async def test_block_url_matches_a_stored_default_port(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    stored = mdb.add("1", url="https://www.linkedin.com:443/jobs/view/9")
    assert await _block(engine, "url", "https://www.linkedin.com/jobs/view/9") == 1
    assert not mdb.exists(stored)
    again = draft(9, url="https://www.linkedin.com:443/jobs/view/9")
    assert (await _ingest_many(engine, [again])).rejected == 1


async def test_a_stored_url_that_cannot_be_parsed_is_skipped(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    """One malformed stored URL must not break a takedown of the others."""
    broken = mdb.add("1", url="http://[::1/x")  # passes the DB check, fails urlsplit
    target = mdb.add("2", url="https://www.linkedin.com/jobs/view/2")
    with pytest.raises(ValueError):
        service.canonical_url("http://[::1/x")

    assert await _block(engine, "url", "https://www.linkedin.com/jobs/view/2?trk=1") == 1

    assert mdb.exists(broken) and not mdb.exists(target)


async def test_a_takedown_waiting_on_an_ingest_gives_up(
    engine: AsyncEngine,
    mdb: MaintenanceDb,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With an ingest holding the shared lock past the timeout, block and unblock fail with a
    clear error (the CLI exits 2) and change nothing."""
    monkeypatch.setattr(service, "BLOCK_LOCK_TIMEOUT_MS", 200)
    ingesting = AsyncSession(engine)
    await service.ingest_draft(ingesting, draft(1), collected_by=None, contributes_to_pool=True)
    try:
        for action in (service.block, service.unblock):
            async with AsyncSession(engine) as session:
                with pytest.raises(service.TakedownBusyError, match="retry"):
                    await action(session, "company", "Acme Corp")
                await session.rollback()

        assert await block_listing.run("block", "company", "Acme Corp") == 2
        assert "retry" in capsys.readouterr().err
    finally:
        await ingesting.rollback()
        await ingesting.close()

    assert mdb.blocks() == []
    # Once the lock is free the takedown works, and the timeout did not leak into the session.
    assert await _block(engine, "company", "Acme Corp") == 0
    assert mdb.blocks() == [("company", None, "acme corp")]


# --------------------------------------------------------------------------- never re-ingested


async def test_a_blocked_draft_is_rejected_and_the_rest_ingest(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    await _block(engine, "company", "Acme Corp")
    await _block(engine, "url", "https://www.linkedin.com/jobs/view/2")
    await _block(engine, "listing", "linkedin:job-3")

    counts = await _ingest_many(
        engine,
        [
            draft(1, company_raw="  ACME corp "),  # blocked company
            draft(2, company_raw="Globex", url="https://WWW.linkedin.com/jobs/view/2?trk=1"),
            draft(3, company_raw="Globex"),  # blocked listing id (job-3)
            draft(4, company_raw="Globex"),
            draft(5, company_raw="Initech"),
        ],
    )

    assert (counts.listings, counts.created, counts.rejected) == (2, 2, 3)
    assert {r[0] for r in mdb.execute("SELECT source_job_id FROM app.listings")} == {
        "job-4",
        "job-5",
    }
    assert mdb.ops("listings.ingested") == [{"listings": 2, "created": 2, "rejected": 3}]


async def test_ingest_draft_returns_none_for_a_blocked_draft(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    await _block(engine, "company", "Acme Corp")
    async with AsyncSession(engine) as session:
        blocked = await service.ingest_draft(
            session, draft(1), collected_by=None, contributes_to_pool=True
        )
        fine = await service.ingest_draft(
            session, draft(2, company_raw="Globex"), collected_by=None, contributes_to_pool=True
        )
        await session.commit()

    assert blocked is None
    assert fine is not None and fine[1] is True
    assert mdb.count() == 1


async def test_a_blocked_listing_cannot_come_back_by_refresh(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    await _ingest_many(engine, [draft(1)])
    assert mdb.count() == 1
    assert await _block(engine, "listing", "linkedin:job-1") == 1

    counts = await _ingest_many(engine, [draft(1)])

    assert counts.rejected == 1 and mdb.count() == 0


async def test_unblock_lets_ingests_succeed_again_and_audits(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    await _block(engine, "company", "Acme Corp")
    assert (await _ingest_many(engine, [draft(1)])).rejected == 1
    audits = len(mdb.audit("listings.unblock"))

    assert await _unblock(engine, "company", " ACME corp ") is True

    assert mdb.blocks() == []
    assert len(mdb.audit("listings.unblock")) == audits + 1
    counts = await _ingest_many(engine, [draft(1)])
    assert (counts.listings, counts.rejected) == (1, 0)


async def test_unblock_of_an_unknown_block_is_a_no_op(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    audits = len(mdb.audit("listings.unblock"))

    assert await _unblock(engine, "company", "Nobody Inc") is False
    assert await _unblock(engine, "listing", "linkedin:404") is False

    assert len(mdb.audit("listings.unblock")) == audits
    assert mdb.ops("listings.unblocked") == []


async def test_block_waits_for_an_ingest_in_flight(
    engine: AsyncEngine, mdb: MaintenanceDb
) -> None:
    """A draft stored by a transaction that began before the block committed is still
    deleted: the block waits for it (they are ordered by an advisory lock), then sees it."""
    ingesting = AsyncSession(engine)
    await service.ingest_draft(ingesting, draft(1), collected_by=None, contributes_to_pool=True)

    blocking = asyncio.create_task(_block(engine, "company", "Acme Corp"))
    await asyncio.sleep(0.5)
    assert not blocking.done()  # waiting on the ingest's shared lock

    await ingesting.commit()
    await ingesting.close()
    assert await asyncio.wait_for(blocking, 10) == 1
    assert mdb.count() == 0

    # And an ingest started after the block committed is refused.
    assert (await _ingest_many(engine, [draft(2)])).rejected == 1


# --------------------------------------------------------------------------- CLI


async def test_cli_block_and_unblock(
    engine: AsyncEngine, mdb: MaintenanceDb, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = mdb.add("1", company="Acme Corp")
    audits = len(mdb.audit("listings.block"))

    assert await block_listing.run("block", "company", "Acme Corp") == 0
    assert "1 Listing(s) deleted" in capsys.readouterr().out
    assert not mdb.exists(listing) and len(mdb.audit("listings.block")) == audits + 1

    assert await block_listing.run("unblock", "company", "Acme Corp") == 0
    assert "lifted" in capsys.readouterr().out
    assert await block_listing.run("unblock", "company", "Acme Corp") == 1
    assert "nothing changed" in capsys.readouterr().out
    assert mdb.blocks() == []


async def test_cli_bad_value_changes_nothing(
    mdb: MaintenanceDb, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await block_listing.run("block", "listing", "no-colon") == 2
    assert "SOURCE:SOURCE_JOB_ID" in capsys.readouterr().err
    assert mdb.blocks() == []


def test_cli_argument_parsing() -> None:
    args = block_listing.parse_args(["block", "--listing", "linkedin:123"])
    assert (args.action, args.kind, args.value, args.yes_prod) == (
        "block",
        "listing",
        "linkedin:123",
        False,
    )
    args = block_listing.parse_args(["unblock", "--url", "https://x.test/a", "--yes-prod"])
    assert (args.action, args.kind, args.yes_prod) == ("unblock", "url", True)
    for bad in (
        [],
        ["block"],
        ["block", "--company", "A", "--url", "https://x.test"],
        ["block", "--company", "  "],
        ["remove", "--company", "A"],
    ):
        with pytest.raises(SystemExit):
            block_listing.parse_args(bad)


def test_cli_refuses_prod_without_yes_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.test")
    get_settings.cache_clear()
    reached: list[tuple] = []

    async def fake_main(action: str, kind: str, value: str) -> int:
        reached.append((action, kind, value))
        return 0

    monkeypatch.setattr(block_listing, "_main", fake_main)
    try:
        assert block_listing.main(["block", "--company", "Acme"]) == 2
        assert reached == []
        assert block_listing.main(["block", "--company", "Acme", "--yes-prod"]) == 0
        assert reached == [("block", "company", "Acme")]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_cli_entry_point_exit_codes(migrated_db: str, mdb: MaintenanceDb) -> None:
    """The real script in a subprocess: block, then unblock twice."""
    env = {**os.environ, "DATABASE_URL": migrated_db, "ENVIRONMENT": "ci"}
    script = str(API_DIR / "scripts" / "block_listing.py")
    listing = mdb.add(str(uuid.uuid4().int)[:8], company="Umbrella Corp")

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, script, *argv],
            capture_output=True, text=True, env=env, cwd=API_DIR, timeout=60,
        )

    blocked = run("block", "--company", "umbrella corp")
    assert blocked.returncode == 0, blocked.stderr
    assert not mdb.exists(listing)
    assert run("unblock", "--company", "Umbrella Corp").returncode == 0
    assert run("unblock", "--company", "Umbrella Corp").returncode == 1
    assert run("block", "--listing", "nocolon").returncode == 2
