"""matching.service: loads the scoring inputs and calls the pure scorer (AD-9).

* Story 3.1: :func:`match_listing` scores one Listing on request and stores nothing.
* Story 3.2: stored scores for each Student's candidate set. :func:`mark_stale` is the ONLY
  recompute trigger (called by the event subscribers registered in ``matching.events``): it
  marks Students stale and enqueues one ``matching.recompute`` job per Student.
  :func:`recompute_for_owner` is that job: it re-scores the candidate set with the one
  scorer, prunes scores that dropped out and feeds ``listings.service.set_relevance``.

Services take the caller's session and never commit. :func:`recompute_for_owner` is a task
body: it opens and commits its own short transactions.
"""

import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import NotFoundError
from app.core.events import (
    ListingsIngested,
    PreferencesChanged,
    ResumeActivated,
    SkillProfileChanged,
)
from app.core.jobs import DEFAULT_QUEUE, PRIORITY_QUEUE, enqueue
from app.core.ops import record_event
from app.modules.listings import service as listings_service
from app.modules.matching import repo
from app.modules.matching.schemas import ScoreBreakdown
from app.modules.matching.scoring import ScoringListing, ScoringProfile, score
from app.modules.matching.tasks import RECOMPUTE
from app.modules.matching.weights import current_weights
from app.modules.profile import service as profile_service
from app.modules.profile.models import Preferences, SkillProfile

logger = logging.getLogger("app.matching")

STALE_EVENT = "matching.marked_stale"
RECOMPUTE_EVENT = "matching.recompute"
SUPERSEDED_EVENT = "matching.recompute_superseded"


@dataclass(frozen=True)
class MatchResult:
    listing_id: uuid.UUID
    score: int
    breakdown: ScoreBreakdown


def build_profile(
    skill_profile: SkillProfile | None, preferences: Preferences | None
) -> ScoringProfile:
    """The :class:`ScoringProfile` of a loaded Skill Profile and Preferences (either may be
    None): the one profile builder of the endpoint and the recompute job."""
    # Array elements may be NULL; drop them (and empty strings) so the scorer never sees one.
    skills = {s for s in profile_service.effective_skills(skill_profile) if s}
    roles = preferences.role_titles if preferences is not None else []
    return ScoringProfile(
        skills=frozenset(skills),
        vocab_version=skill_profile.vocab_version if skill_profile is not None else None,
        role_titles=tuple(r for r in roles or [] if r and r.strip()),
    )


async def scoring_profile(session: AsyncSession, owner_id: uuid.UUID) -> ScoringProfile:
    """The Student's effective skills (custom skills never scored), the Skill Profile's
    ``vocab_version`` and the Preferences' role titles. Either may be missing."""
    skill_profile = await profile_service.get_skill_profile(session, owner_id)
    preferences = await profile_service.get_preferences(session, owner_id)
    return build_profile(skill_profile, preferences)


def _scoring_listing(listing: listings_service.ScoringInput) -> ScoringListing:
    return ScoringListing(
        title=listing.title,
        skills=frozenset(s for s in listing.skills if s),
        vocab_version=listing.vocab_version,
        lexemes=frozenset(lx for lx in listing.lexemes if lx),
    )


async def match_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> MatchResult:
    """Score one Listing visible to ``owner_id`` (``NotFoundError`` otherwise). Read-only."""
    listing = await listings_service.get_scoring_input(session, owner_id, listing_id)
    profile = await scoring_profile(session, owner_id)
    value, breakdown = score(profile, _scoring_listing(listing), current_weights())
    return MatchResult(listing_id, value, breakdown)


# --------------------------------------------------------------------------- stale (Story 3.2)


def recompute_key(owner_id: uuid.UUID, *, priority: bool = False) -> str:
    """``matching.recompute:student:<id>`` on ``default``; priority jobs add ``:priority``,
    because the queueing lock spans queues and a pending default job must never swallow a
    Student's own edit (Spec Change Log)."""
    key = f"{RECOMPUTE}:student:{owner_id}"
    return f"{key}:priority" if priority else key


async def _enqueue_recompute(
    session: AsyncSession, owner_id: uuid.UUID, *, priority: bool
) -> None:
    await enqueue(
        session,
        RECOMPUTE,
        recompute_key(owner_id, priority=priority),
        queue=PRIORITY_QUEUE if priority else DEFAULT_QUEUE,
        owner_id=owner_id,
        priority=int(priority),
    )


async def mark_stale(
    session: AsyncSession,
    *,
    owner_ids: Iterable[uuid.UUID],
    reason: str,
    priority: bool,
) -> int:
    """THE recompute trigger. Sets ``match_state.scores_stale_since`` for every owner (one
    bulk upsert) and enqueues one ``matching.recompute`` per owner: on the ``priority`` queue
    keyed ``matching.recompute:student:<id>:priority`` when ``priority`` (the Student's own
    change; the router drains it after the response), else on ``default`` keyed
    ``matching.recompute:student:<id>``.
    The queueing lock drops a duplicate while one is pending. ``reason`` (a short machine
    name) is the count key of the ``matching.marked_stale`` ops event. On the caller's
    session; never commits. Returns the number of owners marked."""
    owners = sorted(set(owner_ids))
    if not owners:
        return 0
    await repo.mark_stale(session, owners)
    for owner_id in owners:
        await _enqueue_recompute(session, owner_id, priority=priority)
    await record_event(
        session,
        STALE_EVENT,
        owner_id=owners[0] if len(owners) == 1 else None,
        counts={reason: len(owners)},
    )
    return len(owners)


# ---- event subscribers (registered by matching.events)


async def on_resume_activated(session: AsyncSession, event: ResumeActivated) -> None:
    await mark_stale(
        session, owner_ids=[event.owner_id], reason="resume_activated", priority=True
    )


async def on_skill_profile_changed(session: AsyncSession, event: SkillProfileChanged) -> None:
    await mark_stale(
        session, owner_ids=[event.owner_id], reason="skill_profile_changed", priority=True
    )


async def on_preferences_changed(session: AsyncSession, event: PreferencesChanged) -> None:
    await mark_stale(
        session, owner_ids=[event.owner_id], reason="preferences_changed", priority=True
    )


async def on_listings_ingested(session: AsyncSession, event: ListingsIngested) -> None:
    """One ingestion call (one event): when one of its Listings is in the shared pool, every
    Student with a ``match_state`` row, a Preferences filter (role titles, locations or job
    types) or an effective skill (and the collector) is marked stale on the ``default`` queue. When every Listing is private to its
    collector, only the collector is. One bulk upsert and one job per Student, never a job
    per Listing."""
    if not event.listing_ids:
        return
    owners: set[uuid.UUID] = set()
    if event.collected_by is not None:
        owners.add(event.collected_by)
    if await listings_service.has_pool_listing(session, event.listing_ids):
        owners.update(await repo.state_owner_ids(session))
        owners.update(await profile_service.owner_ids_with_matching_setup(session))
    await mark_stale(session, owner_ids=owners, reason="listings_ingested", priority=False)


# --------------------------------------------------------------------------- recompute (3.2)


def has_scoring_setup(
    skill_profile: SkillProfile | None, preferences: Preferences | None
) -> bool:
    """Whether the Student has set any Preferences filter (role titles, locations or job
    types) or has any scorable skill. Without either they get no candidates."""
    if preferences is not None and (
        preferences.role_titles or preferences.locations or preferences.job_types
    ):
        return True
    return any(profile_service.effective_skills(skill_profile))


@dataclass(frozen=True)
class RecomputeResult:
    candidates: int
    deleted: int
    requeued: bool
    # A newer run had already written this Student's scores: nothing was written.
    superseded: bool = False


async def recompute_for_owner(
    owner_id: uuid.UUID, *, priority: bool = False
) -> RecomputeResult:
    """The ``matching.recompute`` job for one Student. Idempotent.

    1. One short read transaction: the start time (database clock), the profile (the same
       builder as the endpoint), the candidate set (``listings.service.candidate_set``, one
       query) and the stored scores.
    2. Every candidate is scored in Python with :func:`~.scoring.score` and the current
       weights, with no transaction open.
    3. One write transaction, under the Student's ``match_state`` row lock (no row means the
       account is gone: nothing is written; a ``last_computed_at`` after this run's start
       means a newer run already wrote: nothing is written and
       ``matching.recompute_superseded`` is recorded). Candidates deleted since the read are
       dropped (the survivors are held ``FOR KEY SHARE``). Then upsert the scores and replace
       their missing-skill rows, delete the Student's scores for Listings no longer in the set,
       set each changed or removed Listing's relevance to its max score over every Student
       (0 when none is left), record ``last_computed_at`` (the start) and
       ``last_candidates``, and a counts-only ``matching.recompute`` ops event.
    4. After the commit, when ``scores_stale_since`` is newer than the start (a change landed
       while this ran), it enqueues itself again on the same queue.
    """
    clock = time.monotonic()
    maker = get_sessionmaker()
    async with maker() as session:
        started = await repo.db_clock(session)
        skill_profile = await profile_service.get_skill_profile(session, owner_id)
        preferences = await profile_service.get_preferences(session, owner_id)
        profile = build_profile(skill_profile, preferences)
        candidates: list[listings_service.CandidateInput] = []
        if has_scoring_setup(skill_profile, preferences):
            candidates = await listings_service.candidate_set(
                session,
                owner_id,
                role_titles=list(preferences.role_titles) if preferences else [],
                locations=list(preferences.locations) if preferences else [],
                job_types=list(preferences.job_types) if preferences else [],
                cap=get_settings().match_candidate_cap,
            )
        previous = await repo.owner_scores(session, owner_id)
        await session.rollback()

    weights = current_weights()
    rows: list[repo.ScoreRow] = []
    for candidate in candidates:
        value, breakdown = score(profile, _scoring_listing(candidate.scoring), weights)
        rows.append(
            repo.ScoreRow(
                listing_id=candidate.listing_id,
                score=value,
                breakdown=breakdown.model_dump(mode="json"),
                algo_version=breakdown.algo_version,
                weights_version=breakdown.weights_version,
                vocab_version=breakdown.vocab_version,
                missing_skills=tuple(s.id for s in breakdown.missing_skills),
            )
        )

    async with maker() as session:
        state = await repo.lock_state(session, owner_id)
        if state is None:
            await session.rollback()
            return RecomputeResult(0, 0, False)
        if state.last_computed_at is not None and state.last_computed_at > started:
            # An older run must never overwrite fresher scores.
            await record_event(
                session, SUPERSEDED_EVENT, owner_id=owner_id, counts={"superseded": 1}
            )
            await session.commit()
            return RecomputeResult(0, 0, False, superseded=True)
        # A candidate deleted since the read (purge, eviction, takedown) would fail the
        # foreign key: keep only the Listings that still exist, held until the commit.
        alive = set(await listings_service.lock_existing(session, [r.listing_id for r in rows]))
        rows = [r for r in rows if r.listing_id in alive]
        computed_at = await repo.db_clock(session)
        await repo.upsert_scores(session, owner_id, rows, computed_at)
        removed = await repo.delete_scores_except(
            session, owner_id, [r.listing_id for r in rows]
        )
        changed = {r.listing_id for r in rows if previous.get(r.listing_id) != r.score}
        changed.update(removed)
        await _refresh_relevance(session, sorted(changed))
        await repo.set_computed(session, owner_id, computed_at=started, candidates=len(rows))
        await record_event(
            session,
            RECOMPUTE_EVENT,
            owner_id=owner_id,
            counts={
                "candidates": len(rows),
                "upserted": len(rows),
                "deleted": len(removed),
                "ms": int((time.monotonic() - clock) * 1000),
            },
        )
        await session.commit()

    requeued = False
    async with maker() as session:
        state = await repo.get_state(session, owner_id)
        if state is not None and state.scores_stale_since is not None and (
            state.scores_stale_since > started
        ):
            await _enqueue_recompute(session, owner_id, priority=priority)
            await session.commit()
            requeued = True
    if requeued:
        logger.info("matching_recompute_requeued", extra={"owner_id": str(owner_id)})
    return RecomputeResult(len(rows), len(removed), requeued)


async def _refresh_relevance(session: AsyncSession, listing_ids: list[uuid.UUID]) -> None:
    """Set each Listing's relevance to its max stored score over every Student (one aggregate
    query; 0 when no score is left). The Listing rows are locked first, in id order, so a
    concurrent recompute cannot write a stale max in between."""
    if not listing_ids:
        return
    await listings_service.lock_existing(session, listing_ids, for_update=True)
    best = await repo.max_scores(session, listing_ids)
    for listing_id in listing_ids:
        try:
            await listings_service.set_relevance(session, listing_id, best.get(listing_id, 0))
        except NotFoundError:
            continue  # deleted meanwhile (purge, eviction, takedown): nothing to rank
