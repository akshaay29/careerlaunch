"""FR-9, NFR-3, AD-9 (Story 3.2): stored Match Scores, ``mark_stale`` and the
``matching.recompute`` job. One test (or more) per row of the spec's I/O matrix."""

import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import events as core_events
from app.core.events import (
    ListingsIngested,
    PreferencesChanged,
    ResumeActivated,
    SkillProfileChanged,
)
from app.core.jobs import (
    PRIORITY_QUEUE,
    drain_priority_after_response,
    flush_outbox,
    run_worker,
)
from app.modules.listings import service as listings_service
from app.modules.matching import service as matching_service
from app.modules.matching import tasks as matching_tasks
from app.modules.matching.scoring import ScoringListing, score
from app.modules.matching.weights import current_weights
from tests.conftest import AccountFactory
from tests.listing_fixtures import draft
from tests.match_fixtures import RECOMPUTE, MatchDb, ingest, mark, recompute

pytestmark = pytest.mark.usefixtures("clean_jobs")

FAKE_RESUME_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")


@pytest.fixture
def db(migrated_db: str) -> MatchDb:
    match_db = MatchDb(migrated_db)
    match_db.clear()
    yield match_db
    match_db.clear()


def _key(owner: uuid.UUID, *, priority: bool = False) -> str:
    key = f"{RECOMPUTE}:student:{owner}"
    return f"{key}:priority" if priority else key


def _student(accounts: AccountFactory, db: MatchDb, roles=("Backend Developer",), skills=()):
    owner = accounts.create()
    if roles is not None:
        db.set_preferences(owner, roles)
    if skills is not None:
        db.set_profile(owner, skills)
    return owner


async def _seed(engine: AsyncEngine, db: MatchDb, *titles: str, **fields) -> list[uuid.UUID]:
    """Pool Listings (System-collected) with the given titles; returns their ids in order."""
    start = len(db.execute("SELECT 1 FROM app.listings"))
    drafts = [
        draft(start + i, title=title, **fields) for i, title in enumerate(titles, start=1)
    ]
    await ingest(engine, drafts)
    return [db.id_of(d.source_job_id) for d in drafts]


# --------------------------------------------------------------------------- mark_stale


async def test_mark_stale_upserts_state_and_enqueues_by_queue(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    a, b = accounts.create(), accounts.create()
    before = db.now()
    async with AsyncSession(engine) as session:
        marked = await matching_service.mark_stale(
            session, owner_ids=[a, a], reason="skill_profile_changed", priority=True
        )
        assert marked == 1
        await matching_service.mark_stale(
            session, owner_ids=[b], reason="listings_ingested", priority=False
        )
        # Never commits: nothing is visible outside the session yet.
        assert db.state(a) is None and db.outbox() == []
        await session.commit()

    stale_a, computed, candidates = db.state(a)  # type: ignore[misc]
    assert stale_a >= before and computed is None and candidates is None
    rows = {key: (queue, args) for key, queue, args in db.outbox()}
    assert rows == {
        _key(a, priority=True): (PRIORITY_QUEUE, {"owner_id": str(a), "priority": 1}),
        _key(b): ("default", {"owner_id": str(b), "priority": 0}),
    }
    (count_a,) = [c for o, c in db.ops("matching.marked_stale") if o == a]
    assert count_a == {"skill_profile_changed": 1}


async def test_mark_stale_with_no_owners_does_nothing(engine: AsyncEngine, db: MatchDb) -> None:
    async with AsyncSession(engine) as session:
        assert await matching_service.mark_stale(
            session, owner_ids=[], reason="x", priority=True
        ) == 0
        await session.commit()
    assert db.outbox() == [] and db.ops("matching.marked_stale") == []


# --------------------------------------------------------------------------- subscribers


@pytest.mark.parametrize(
    ("event_type", "handler"),
    [
        (ResumeActivated, matching_service.on_resume_activated),
        (SkillProfileChanged, matching_service.on_skill_profile_changed),
        (PreferencesChanged, matching_service.on_preferences_changed),
        (ListingsIngested, matching_service.on_listings_ingested),
    ],
)
def test_subscribers_are_registered(event_type, handler) -> None:
    assert handler in core_events._subscribers[event_type]


async def test_skill_edit_marks_stale_on_priority_and_rescoring_reflects_it(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: skill edit."""
    (listing,) = await _seed(engine, db, "Backend Developer Intern")
    db.set_skills(listing, ["python", "docker"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    first = db.scores(owner)[listing]
    db.clear_outbox()

    db.set_profile(owner, ["python", "docker"])
    before = db.now()
    async with AsyncSession(engine) as session:
        await core_events.publish(session, SkillProfileChanged(owner_id=owner))
        await session.commit()
    assert db.state(owner)[0] >= before  # type: ignore[index]
    assert db.outbox() == [(_key(owner, priority=True), PRIORITY_QUEUE, {"owner_id": str(owner), "priority": 1})]

    await recompute(owner)
    assert db.scores(owner)[listing] > first
    assert db.missing(owner) == []


async def test_resume_activated_marks_stale_on_priority(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: Resume activated (the matching subscriber alone: profile's own subscriber
    needs a stored Resume)."""
    owner = accounts.create()
    async with AsyncSession(engine) as session:
        await matching_service.on_resume_activated(
            session, ResumeActivated(owner_id=owner, resume_id=FAKE_RESUME_ID)
        )
        await session.commit()
    assert db.state(owner)[0] is not None  # type: ignore[index]
    assert db.outbox() == [(_key(owner, priority=True), PRIORITY_QUEUE, {"owner_id": str(owner), "priority": 1})]
    assert [c for o, c in db.ops("matching.marked_stale") if o == owner] == [
        {"resume_activated": 1}
    ]


async def test_preferences_change_swaps_the_candidate_set(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: Preferences save from "Backend" to "Data Analyst"."""
    backend, analyst = await _seed(engine, db, "Backend Engineer", "Data Analyst Intern")
    owner = _student(accounts, db, roles=["Backend"], skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == {backend}

    db.clear_outbox()
    db.set_preferences(owner, ["Data Analyst"])
    async with AsyncSession(engine) as session:
        await core_events.publish(session, PreferencesChanged(owner_id=owner))
        await session.commit()
    assert [k for k, q, _ in db.outbox()].count(_key(owner, priority=True)) == 1
    result = await recompute(owner)
    assert set(db.scores(owner)) == {analyst}
    assert result.deleted == 1


# --------------------------------------------------------------------------- queueing


async def test_duplicate_triggers_fold_into_one_pending_job(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: three edits before the drain leave one pending job; the run sees the last."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    db.set_skills(listing, ["python", "docker", "aws"])
    owner = _student(accounts, db, skills=[])
    db.clear_outbox()
    for skill_set in (["python"], ["python", "docker"], ["python", "docker", "aws"]):
        db.set_profile(owner, skill_set)
        async with AsyncSession(engine) as session:
            await core_events.publish(session, SkillProfileChanged(owner_id=owner))
            await session.commit()
    assert len(db.outbox()) == 3

    flushed = await flush_outbox()
    assert (flushed.deferred, flushed.deduplicated) == (1, 2)
    pending = db.execute(
        "SELECT count(*) FROM app.procrastinate_jobs WHERE queueing_lock = %s AND status = 'todo'",
        (_key(owner, priority=True),),
    )
    assert pending == [(1,)]

    await run_worker(PRIORITY_QUEUE)
    assert db.missing(owner) == []
    (breakdown,) = [b for _, _, b, *_ in db.score_rows(owner)]
    assert [s["id"] for s in breakdown["matched_skills"]] == ["aws", "docker", "python"]


@pytest.mark.parametrize("priority", [True, False])
async def test_stale_during_a_run_requeues_on_the_same_queue(
    engine: AsyncEngine,
    accounts: AccountFactory,
    db: MatchDb,
    monkeypatch: pytest.MonkeyPatch,
    priority: bool,
) -> None:
    """Matrix: ``mark_stale`` lands while the job runs (run through the task entry point)."""
    await _seed(engine, db, "Backend Developer")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner, priority=priority)
    db.clear_outbox()

    real = listings_service.candidate_set

    async def candidate_set_then_mark(*args, **kwargs):
        result = await real(*args, **kwargs)
        # Another transaction, committed mid-run, on the other queue.
        await mark(engine, owner, priority=not priority)
        return result

    monkeypatch.setattr(listings_service, "candidate_set", candidate_set_then_mark)
    await matching_tasks.recompute(owner_id=owner, priority=int(priority))
    queue = PRIORITY_QUEUE if priority else "default"
    args = {"owner_id": str(owner), "priority": int(priority)}
    # The mark's own job (other queue) and the re-enqueue on this run's queue and key.
    assert sorted(db.outbox()) == sorted(
        [
            (_key(owner, priority=priority), queue, args),
            (
                _key(owner, priority=not priority),
                "default" if priority else PRIORITY_QUEUE,
                {"owner_id": str(owner), "priority": int(not priority)},
            ),
        ]
    )

    monkeypatch.setattr(listings_service, "candidate_set", real)
    db.clear_outbox()
    assert (await recompute(owner, priority=priority)).requeued is False
    assert db.outbox() == []


async def test_a_pending_default_job_never_swallows_a_priority_mark(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Spec Change Log: a Student's edit gets its own priority job even while an ingest's
    default job is pending, and the after-response drain refreshes the scores."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner, priority=False)
    assert (await flush_outbox()).deferred == 1  # the default job is pending in Procrastinate

    await mark(engine, owner, priority=True)
    flushed = await flush_outbox()
    assert (flushed.deferred, flushed.deduplicated) == (1, 0)
    await drain_priority_after_response()
    assert listing in db.scores(owner)


async def test_a_listing_deleted_mid_run_is_dropped(
    engine: AsyncEngine,
    accounts: AccountFactory,
    db: MatchDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keep, gone = await _seed(engine, db, "Backend Developer", "Backend Developer II")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    real = matching_service.score
    deleted: list[bool] = []

    def score_then_delete(*args, **kwargs):
        if not deleted:  # between the read and the write
            db.execute("DELETE FROM app.listings WHERE id = %s", (gone,))
            deleted.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(matching_service, "score", score_then_delete)
    result = await recompute(owner)
    assert deleted and set(db.scores(owner)) == {keep}
    assert result.candidates == 1


async def test_an_older_run_never_overwrites_a_newer_one(
    engine: AsyncEngine,
    accounts: AccountFactory,
    db: MatchDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(engine, db, "Backend Developer")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    real = listings_service.candidate_set

    async def candidate_set_then_newer_run(*args, **kwargs):
        result = await real(*args, **kwargs)
        # A newer run started (and wrote) after this one began.
        db.execute(
            "UPDATE app.match_state SET last_computed_at = clock_timestamp() WHERE owner_id = %s",
            (owner,),
        )
        return result

    monkeypatch.setattr(listings_service, "candidate_set", candidate_set_then_newer_run)
    result = await recompute(owner)
    assert result.superseded is True
    assert db.scores(owner) == {}
    assert [c for o, c in db.ops("matching.recompute_superseded") if o == owner] == [
        {"superseded": 1}
    ]
    assert [o for o, _ in db.ops("matching.recompute")] == []


# --------------------------------------------------------------------------- ingest


async def test_ingest_marks_each_affected_student_once(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: 40 new pool Listings for 3 Students with Preferences: 3 jobs, not 40 or 120."""
    students = [_student(accounts, db, skills=["python"]) for _ in range(3)]
    db.clear_outbox()
    await _seed(engine, db, *(f"Backend Developer {n}" for n in range(40)))

    rows = db.outbox()
    keys = [k for k, _, _ in rows]
    assert len(keys) == len(set(keys))  # one job per Student, never per Listing
    for owner in students:
        assert keys.count(_key(owner)) == 1
    assert {q for _, q, _ in rows} == {"default"}
    assert all(db.state(owner) is not None for owner in students)
    (counts,) = [c for _, c in db.ops("matching.marked_stale")]
    assert counts == {"listings_ingested": len(keys)}

    await recompute(students[0], priority=False)
    assert len(db.scores(students[0])) == 40


async def test_ingest_marks_students_known_only_by_match_state(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, roles=None, skills=["python"])  # no Preferences row
    await mark(engine, owner)
    db.clear_outbox()
    await _seed(engine, db, "Backend Developer")
    assert _key(owner) in [k for k, _, _ in db.outbox()]


async def test_private_ingest_marks_only_the_collector(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: a collector's private Listings (no pool consent) mark only the collector."""
    bystander = _student(accounts, db, skills=["python"])
    collector = _student(accounts, db, skills=["python"])
    db.clear_outbox()
    counts = await ingest(engine, [draft(1, title="Backend Developer")], collected_by=collector)
    assert counts.listings == 1
    assert [k for k, _, _ in db.outbox()] == [_key(collector)]
    assert db.state(bystander) is None


async def test_pool_ingest_by_a_student_marks_everyone(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    bystander = _student(accounts, db, skills=["python"])
    collector = accounts.create()  # no Preferences: marked as the collector
    db.grant_pool(collector)
    db.clear_outbox()
    await ingest(engine, [draft(1, title="Backend Developer")], collected_by=collector)
    keys = [k for k, _, _ in db.outbox()]
    assert _key(bystander) in keys and _key(collector) in keys


# --------------------------------------------------------------------------- candidate set


async def test_another_students_private_listings_are_never_candidates(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: visibility."""
    owner = _student(accounts, db, skills=["python"])
    other = accounts.create()
    pool = await _seed(engine, db, "Backend Developer")
    await ingest(engine, [draft(50, title="Backend Developer Private")], collected_by=other)
    async with AsyncSession(engine) as session:
        await listings_service.ingest_draft(
            session,
            draft(51, source="manual", title="Backend Developer Manual"),
            collected_by=other,
            contributes_to_pool=False,
        )
        await session.commit()
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == set(pool)


async def test_expired_listing_drops_out_on_the_next_recompute(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: expired."""
    keep, expire = await _seed(engine, db, "Backend Developer", "Backend Developer II")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == {keep, expire}

    db.execute("UPDATE app.listings SET expired_at = now() WHERE id = %s", (expire,))
    result = await recompute(owner)
    assert set(db.scores(owner)) == {keep} and result.deleted == 1


async def test_role_location_and_job_type_filters(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, skills=["python"])
    db.set_preferences(
        owner, ["Backend Developer"], locations=["bengaluru"], job_types=["internship"]
    )
    fits = await _seed(engine, db, "Backend Developer Intern", location_raw="Bengaluru, India")
    typo = await _seed(engine, db, "Backend Develper", location_raw="Bengaluru")  # trigram
    stemmed = await _seed(engine, db, "Developers, Backend", location_raw="Bengaluru")
    no_type = await _seed(engine, db, "Backend Developer", location_raw="Bengaluru", job_type=None)
    await _seed(engine, db, "Data Analyst", location_raw="Bengaluru")  # role
    await _seed(engine, db, "Backend Developer", location_raw="Mumbai")  # location
    await _seed(engine, db, "Backend Developer", location_raw="Bengaluru", job_type="full_time")
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == {*fits, *typo, *stemmed, *no_type}


async def test_empty_preference_lists_do_not_filter(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, roles=None, skills=["python"])  # skills only
    ids = await _seed(engine, db, "Backend Developer", "Data Analyst", "Chef")
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == set(ids)


async def test_cap_keeps_the_newest_and_every_manual_listing(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: 700 matching Listings: the 500 newest are scored, plus the manual ones."""
    owner = _student(accounts, db, skills=["python"])
    db.execute(
        """
        INSERT INTO app.listings (id, source, source_job_id, url, title, vocab_version,
                                  search_doc, first_seen_at, last_seen_at)
        SELECT gen_random_uuid(), 'linkedin', 'bulk-' || n, 'https://example.test/' || n,
               'Backend Developer ' || n, 'v1',
               setweight(to_tsvector('english', 'Backend Developer ' || n), 'A'),
               now() - n * interval '1 minute', now()
        FROM generate_series(1, 700) AS n
        """
    )
    async with AsyncSession(engine) as session:
        for n in (1, 2):
            await listings_service.ingest_draft(
                session,
                draft(900 + n, source="manual", title=f"Old Manual {n}"),
                collected_by=owner,
                contributes_to_pool=False,
            )
        await session.commit()
    db.execute(
        "UPDATE app.listings SET first_seen_at = now() - interval '30 days' "
        "WHERE source = 'manual'"
    )
    await mark(engine, owner)
    result = await recompute(owner)
    assert result.candidates == 502
    scored = set(db.scores(owner))
    newest = {
        r[0]
        for r in db.execute(
            "SELECT id FROM app.listings WHERE source_job_id LIKE 'bulk-%%' "
            "ORDER BY first_seen_at DESC LIMIT 500"
        )
    }
    manual = {r[0] for r in db.execute("SELECT id FROM app.listings WHERE source = 'manual'")}
    assert scored == newest | manual
    assert db.state(owner)[2] == 502  # type: ignore[index]


async def test_empty_profile_gets_no_candidates_and_loses_its_scores(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: no Preferences and no Skill Profile."""
    await _seed(engine, db, "Backend Developer", "Data Analyst")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert db.scores(owner)

    db.execute("DELETE FROM app.preferences WHERE owner_id = %s", (owner,))
    db.execute("DELETE FROM app.skill_profiles WHERE owner_id = %s", (owner,))
    result = await recompute(owner)
    assert result.candidates == 0 and result.deleted == 1
    assert db.scores(owner) == {} and db.state(owner)[2] == 0  # type: ignore[index]


async def test_a_never_marked_student_is_a_no_op(accounts: AccountFactory, db: MatchDb) -> None:
    owner = accounts.create()
    assert await recompute(owner) == matching_service.RecomputeResult(0, 0, False)
    assert db.state(owner) is None and db.ops("matching.recompute") == []


# --------------------------------------------------------------------------- stored rows


async def test_stored_scores_equal_the_scorer(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    (listing,) = await _seed(engine, db, "Backend Developer Intern")
    db.set_skills(listing, ["python", "docker", "aws"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)

    async with AsyncSession(engine) as session:
        profile = await matching_service.scoring_profile(session, owner)
        loaded = await listings_service.get_scoring_input(session, owner, listing)
    expected, breakdown = score(
        profile,
        ScoringListing(
            loaded.title, frozenset(loaded.skills), loaded.vocab_version, frozenset(loaded.lexemes)
        ),
        current_weights(),
    )
    ((lid, value, stored, algo, weights, vocab, _),) = db.score_rows(owner)
    assert (lid, value) == (listing, expected)
    assert stored == breakdown.model_dump(mode="json")
    assert (algo, weights, vocab) == (
        breakdown.algo_version,
        breakdown.weights_version,
        breakdown.vocab_version,
    )


async def test_missing_skill_rows_follow_the_breakdown(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: missing [docker, aws]: two rows, replaced on the next recompute."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    db.set_skills(listing, ["python", "docker", "aws"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert db.missing(owner) == [(listing, "aws"), (listing, "docker")]

    db.set_profile(owner, ["python", "docker"])
    await recompute(owner)
    assert db.missing(owner) == [(listing, "aws")]


async def test_relevance_is_the_max_score_over_students(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: A scores L higher than B; relevance is A's score, then B's once A's goes."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    db.set_skills(listing, ["python", "docker"])
    a = _student(accounts, db, skills=["python", "docker"])
    b = _student(accounts, db, skills=["python"])
    await mark(engine, a, b)
    await recompute(a)
    await recompute(b)
    score_a, score_b = db.scores(a)[listing], db.scores(b)[listing]
    assert score_a > score_b
    assert db.relevance(listing) == score_a

    db.set_preferences(a, ["Chef"])
    db.execute("DELETE FROM app.skill_profiles WHERE owner_id = %s", (a,))
    await recompute(a)
    assert listing not in db.scores(a)
    assert db.relevance(listing) == score_b

    db.execute("DELETE FROM app.preferences WHERE owner_id = %s", (b,))
    db.execute("DELETE FROM app.skill_profiles WHERE owner_id = %s", (b,))
    await recompute(b)
    assert db.relevance(listing) == 0


async def test_purged_listing_cascades(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: Listing purged."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    db.set_skills(listing, ["python", "docker"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert db.scores(owner) and db.missing(owner)

    db.execute("DELETE FROM app.listings WHERE id = %s", (listing,))
    assert db.scores(owner) == {} and db.missing(owner) == []


async def test_deleted_account_cascades(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: account deleted."""
    (listing,) = await _seed(engine, db, "Backend Developer")
    db.set_skills(listing, ["python", "docker"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert db.scores(owner) and db.missing(owner) and db.state(owner)

    db.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert db.scores(owner) == {} and db.missing(owner) == [] and db.state(owner) is None
    # A job still queued for them is a no-op.
    assert (await recompute(owner)).candidates == 0


async def test_recompute_is_idempotent(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: the job runs twice with no change."""
    ids = await _seed(engine, db, "Backend Developer", "Backend Developer II")
    db.set_skills(ids[0], ["python", "aws"])
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    first = [row[:-1] for row in db.score_rows(owner)]
    missing = db.missing(owner)
    relevance = [db.relevance(i) for i in ids]

    result = await recompute(owner)
    assert result.deleted == 0
    assert [row[:-1] for row in db.score_rows(owner)] == first
    assert db.missing(owner) == missing
    assert [db.relevance(i) for i in ids] == relevance


async def test_each_run_records_one_counts_only_event(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Matrix: ops."""
    await _seed(engine, db, "Backend Developer", "Backend Developer II")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    before = db.now()
    await recompute(owner)
    await recompute(owner)
    events = [c for o, c in db.ops("matching.recompute") if o == owner]
    assert len(events) == 2
    for counts in events:
        assert set(counts) == {"candidates", "upserted", "deleted", "ms"}
        assert counts["candidates"] == 2 and counts["upserted"] == 2
    stale, computed, candidates = db.state(owner)  # type: ignore[misc]
    assert computed >= before > stale and candidates == 2


# --------------------------------------------------------------------------- review pass 1


async def test_ingest_marks_students_with_only_skills_or_other_filters(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """Students from before 0017 (no ``match_state``, no role titles) but with an effective
    skill or a location / job type filter are marked; one whose skills are all removed is
    not."""
    skills_only = _student(accounts, db, roles=None, skills=["python"])
    location_only = _student(accounts, db, roles=None, skills=None)
    db.set_preferences(location_only, locations=["Bengaluru"])
    job_type_only = _student(accounts, db, roles=None, skills=None)
    db.set_preferences(job_type_only, job_types=["internship"])
    nothing = _student(accounts, db, roles=None, skills=None)
    db.set_preferences(nothing)
    db.set_profile(nothing, ["python"], removed=["python"])
    db.clear_outbox()
    await _seed(engine, db, "Backend Developer")
    keys = [k for k, _, _ in db.outbox()]
    for owner in (skills_only, location_only, job_type_only):
        assert _key(owner) in keys
    assert _key(nothing) not in keys


async def test_empty_location_is_kept_by_a_location_filter(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, skills=["python"])
    db.set_preferences(owner, ["Backend Developer"], locations=["Pune"])
    unknown = await _seed(engine, db, "Backend Developer", location_raw="")
    await _seed(engine, db, "Backend Developer", location_raw="Mumbai")
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == set(unknown)


async def test_role_titles_without_words_match_no_pool_listing(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, roles=["'", "!"], skills=["python"])
    await _seed(engine, db, "Backend Developer", "Data Analyst")
    async with AsyncSession(engine) as session:
        await listings_service.ingest_draft(
            session,
            draft(90, source="manual", title="Anything At All"),
            collected_by=owner,
            contributes_to_pool=False,
        )
        await session.commit()
    await mark(engine, owner)
    result = await recompute(owner)
    assert set(db.scores(owner)) == {db.id_of("job-90")} and result.candidates == 1


async def test_role_titles_with_query_syntax_do_not_raise(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, roles=["C++ & (dev):*", "'", "!"], skills=["python"])
    await _seed(engine, db, "C++ Developer", "Data Analyst")
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == {db.id_of("job-1")}


async def test_own_manual_listing_matching_the_role_is_scored_once(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    owner = _student(accounts, db, skills=["python"])
    async with AsyncSession(engine) as session:
        await listings_service.ingest_draft(
            session,
            draft(91, source="manual", title="Backend Developer"),
            collected_by=owner,
            contributes_to_pool=False,
        )
        await session.commit()
    await mark(engine, owner)
    result = await recompute(owner)
    assert result.candidates == 1
    assert set(db.scores(owner)) == {db.id_of("job-91")}


async def test_a_pool_manual_listing_is_a_candidate(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    """A ``manual`` Listing shared to the pool (no visibility owner) goes through the filters
    like any pool Listing (the manual predicate is NULL-safe)."""
    owner = _student(accounts, db, skills=["python"])
    async with AsyncSession(engine) as session:
        await listings_service.ingest_draft(
            session,
            draft(92, source="manual", title="Backend Developer"),
            collected_by=None,
            contributes_to_pool=True,
        )
        await session.commit()
    await mark(engine, owner)
    await recompute(owner)
    assert set(db.scores(owner)) == {db.id_of("job-92")}


async def test_empty_preferences_row_and_empty_skill_profile_give_no_candidates(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    await _seed(engine, db, "Backend Developer")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    assert db.scores(owner)

    db.set_preferences(owner)  # the row stays, every list empty
    db.set_profile(owner, [])
    result = await recompute(owner)
    assert result.candidates == 0 and result.deleted == 1 and db.scores(owner) == {}


async def test_refresh_relevance_skips_a_deleted_listing(
    engine: AsyncEngine, accounts: AccountFactory, db: MatchDb
) -> None:
    keep, gone = await _seed(engine, db, "Backend Developer", "Backend Developer II")
    owner = _student(accounts, db, skills=["python"])
    await mark(engine, owner)
    await recompute(owner)
    db.execute("DELETE FROM app.listings WHERE id = %s", (gone,))
    db.execute("UPDATE app.listings SET relevance = 0 WHERE id = %s", (keep,))
    async with AsyncSession(engine) as session:
        # The NotFoundError of the deleted Listing is skipped; the other one is refreshed.
        await matching_service._refresh_relevance(session, sorted([keep, gone]))
        await session.commit()
    assert db.relevance(keep) == db.scores(owner)[keep]
