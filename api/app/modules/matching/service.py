"""matching.service: loads the scoring inputs and calls the pure scorer (AD-9).

* Story 3.1: :func:`match_listing` scores one Listing on request and stores nothing. Story
  3.4: it returns the Student's stored score and breakdown when one exists for a Listing they
  can still see; :func:`match_scores` is the batch (search results, no breakdowns) and
  :func:`record_breakdown_opened` the count-only "Why this score" event.
* Story 3.3: :func:`feed` is the Student's ranked feed: stored scores (``ready``), or, before
  any exist, the top candidates scored on request and never stored (``provisional``), or
  ``setup_needed``.
* Story 3.2: stored scores for each Student's candidate set. :func:`mark_stale` is the ONLY
  recompute trigger (called by the event subscribers registered in ``matching.events``): it
  marks Students stale and enqueues one ``matching.recompute`` job per Student.
  :func:`recompute_for_owner` is that job: it re-scores the candidate set with the one
  scorer, prunes scores that dropped out and feeds ``listings.service.set_relevance``.

Services take the caller's session and never commit. :func:`recompute_for_owner` is a task
body: it opens and commits its own short transactions.
"""

import base64
import binascii
import logging
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import InvalidInputError, NotFoundError
from app.core.events import (
    ListingsIngested,
    PreferencesChanged,
    ResumeActivated,
    SkillProfileChanged,
)
from app.core.jobs import DEFAULT_QUEUE, PRIORITY_QUEUE, enqueue, job_pending
from app.core.ops import record_event
from app.modules.listings import service as listings_service
from app.modules.matching import repo
from app.modules.matching.schemas import MatchSource, ScoreBreakdown
from app.modules.matching.scoring import ScoringListing, ScoringProfile, score
from app.modules.matching.tasks import RECOMPUTE
from app.modules.matching.weights import current_weights
from app.modules.profile import service as profile_service
from app.modules.profile.models import Preferences, SkillProfile

logger = logging.getLogger("app.matching")

STALE_EVENT = "matching.marked_stale"
RECOMPUTE_EVENT = "matching.recompute"
SUPERSEDED_EVENT = "matching.recompute_superseded"


BREAKDOWN_OPENED_EVENT = "matching.breakdown_opened"
# The most distinct Listing ids one ``getMatchScores`` call may ask for, and the most raw
# ``ids`` values (duplicates included) the router accepts before de-duplicating.
MAX_BATCH_IDS = 50
MAX_BATCH_RAW_IDS = 200


@dataclass(frozen=True)
class MatchResult:
    listing_id: uuid.UUID
    score: int
    breakdown: ScoreBreakdown
    source: MatchSource = "on_request"


@dataclass(frozen=True)
class ScoreItem:
    listing_id: uuid.UUID
    score: int
    source: MatchSource


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


def _stored_breakdown(value: int, raw: object) -> ScoreBreakdown | None:
    """The stored breakdown, or None when it is not a valid one whose points sum to the
    stored score (the Listing is then scored on request instead)."""
    try:
        breakdown = ScoreBreakdown.model_validate(raw)
    except ValidationError:
        return None
    if sum(c.points for c in breakdown.components) != value:
        return None
    return breakdown


async def match_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> MatchResult:
    """The Match Score of one Listing visible to ``owner_id`` and not expired
    (``NotFoundError`` otherwise): the stored score and breakdown when the Student has one
    (``stored``), else scored now with the one scorer (``on_request``). Read-only: nothing is
    ever stored here."""
    stored = await repo.shown_scores(session, owner_id, [listing_id])
    if listing_id in stored:
        value, raw = stored[listing_id]
        breakdown = _stored_breakdown(value, raw)
        if breakdown is not None:
            return MatchResult(listing_id, value, breakdown, "stored")
    listing = await listings_service.get_scoring_input(session, owner_id, listing_id)
    profile = await scoring_profile(session, owner_id)
    value, breakdown = score(profile, _scoring_listing(listing), current_weights())
    return MatchResult(listing_id, value, breakdown, "on_request")


async def match_scores(
    session: AsyncSession, owner_id: uuid.UUID, listing_ids: Iterable[uuid.UUID]
) -> list[ScoreItem]:
    """The Match Scores of the given Listings (de-duplicated, at most
    :data:`MAX_BATCH_IDS`, else :class:`InvalidInputError`) that are visible to ``owner_id``
    and not expired, in request order; the others are silently left out. Stored scores come
    from one query (a stored row whose breakdown is invalid or does not sum to its score is
    scored now instead, as in :func:`match_listing`); the rest are scored now in one batch
    (one scoring-input query, one profile load) and never stored. Read-only."""
    ids = list(dict.fromkeys(listing_ids))
    if len(ids) > MAX_BATCH_IDS:
        raise InvalidInputError(
            ["query", "ids"], f"Ask for at most {MAX_BATCH_IDS} Listings at a time."
        )
    if not ids:
        return []
    stored = await repo.shown_scores(session, owner_id, ids)
    found: dict[uuid.UUID, tuple[int, MatchSource]] = {
        listing_id: (value, "stored")
        for listing_id, (value, raw) in stored.items()
        if _stored_breakdown(value, raw) is not None
    }
    rest = [listing_id for listing_id in ids if listing_id not in found]
    inputs = await listings_service.scoring_inputs(session, owner_id, rest) if rest else {}
    if inputs:
        profile = await scoring_profile(session, owner_id)
        weights = current_weights()
        for listing_id, listing in inputs.items():
            value, _ = score(profile, _scoring_listing(listing), weights)
            found[listing_id] = (value, "on_request")
    return [ScoreItem(lid, *found[lid]) for lid in ids if lid in found]


async def record_breakdown_opened(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> None:
    """Record that the Student opened "Why this score" for a Listing they can see
    (``NotFoundError`` otherwise, recording nothing): one ``matching.breakdown_opened`` ops
    event with the Student's ``owner_id`` and ``{"opened": 1}``, and no Listing, score or
    skill. Never commits."""
    await listings_service.get_listing(session, owner_id, listing_id)
    await record_event(session, BREAKDOWN_OPENED_EVENT, owner_id=owner_id, counts={"opened": 1})


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


# --------------------------------------------------------------------------- feed (Story 3.3)

DEFAULT_THRESHOLD = 60
FEED_FIRST_OPEN = "feed_first_open"
# A pending priority mark younger than this is trusted; an older one (its job may have failed)
# is made again.
FEED_REMARK_AFTER = timedelta(minutes=15)

FeedState = Literal["ready", "provisional", "setup_needed"]


@dataclass(frozen=True)
class FeedEntry:
    item: listings_service.BrowseItem
    score: int
    below_threshold: bool


@dataclass(frozen=True)
class FeedPage:
    state: FeedState
    threshold: int
    items: list[FeedEntry]
    next_cursor: str | None
    # The toggle is off and at least one Listing the feed could show scores below the threshold.
    hidden_below_threshold: bool = False
    # A priority recompute was queued on the caller's session: the router commits it and
    # starts the after-response priority drain.
    queued: bool = False


def encode_feed_cursor(key: repo.FeedKey) -> str:
    posted = key.posted_at.isoformat() if key.posted_at is not None else ""
    raw = f"{key.score}|{posted}|{key.first_seen_at.isoformat()}|{key.listing_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _aware(stamp: str) -> datetime:
    at = datetime.fromisoformat(stamp)
    if at.tzinfo is None:
        raise ValueError("naive timestamp")
    return at


def decode_feed_cursor(cursor: str) -> repo.FeedKey:
    """The keyset of an opaque feed cursor; :class:`InvalidInputError` (422) otherwise."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        score_text, posted, first_seen, listing_id = raw.split("|")
        if not score_text.isdigit():
            raise ValueError("score")
        value = int(score_text)
        if not 0 <= value <= 100:
            raise ValueError("score range")
        return repo.FeedKey(
            score=value,
            posted_at=_aware(posted) if posted else None,
            first_seen_at=_aware(first_seen),
            listing_id=uuid.UUID(listing_id),
        )
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInputError(["query", "cursor"], listings_service.INVALID_CURSOR) from exc


# Sorts before every real timestamp, so ``posted_at`` NULL comes last under ``reverse=True``.
_NO_POST = datetime.min.replace(tzinfo=UTC)


def _feed_sort_key(entry: FeedEntry) -> tuple:
    """``score DESC, posted_at DESC NULLS LAST, first_seen_at DESC, id DESC`` (with
    ``reverse=True``), the same order as the stored feed."""
    listing = entry.item.listing
    return (
        entry.score,
        listing.posted_at if listing.posted_at is not None else _NO_POST,
        listing.first_seen_at,
        listing.id,
    )


async def _needs_priority_mark(session: AsyncSession, owner_id: uuid.UUID) -> bool:
    """Whether the provisional feed should mark the Student stale on the ``priority`` queue.

    No when (a) a recompute already ran after the last mark (``last_computed_at`` not older
    than ``scores_stale_since``) and stored nothing, so marking again would only repeat it; or
    (b) the last mark is younger than :data:`FEED_REMARK_AFTER` and the Student's priority
    recompute is still waiting (in the outbox or Procrastinate ``todo``). Otherwise yes: no
    mark yet, only a default-queue job pending, or an old mark whose job may have failed. A
    duplicate priority job is folded by its queueing lock."""
    state = await repo.get_state(session, owner_id)
    if state is None:
        return True
    stale, computed = state.scores_stale_since, state.last_computed_at
    if computed is not None and (stale is None or computed >= stale):
        return False
    if stale is None or stale <= await repo.db_clock(session) - FEED_REMARK_AFTER:
        return True
    return not await job_pending(session, recompute_key(owner_id, priority=True))


async def feed(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
    show_below_threshold: bool,
) -> FeedPage:
    """The Student's ranked feed (Story 3.3). Never commits.

    * ``setup_needed``: no Preferences filter and no scorable skill (:func:`has_scoring_setup`);
      ``match_scores`` is not queried.
    * ``ready``: the Student has a stored score for a Listing they can see, or a cursor was
      given (a cursor never falls back to the provisional page: an exhausted one gives an
      empty page). One page of :func:`repo.feed_page` (visible, unexpired Listings only;
      ``score >= threshold`` unless ``show_below_threshold``), with a keyset cursor.
    * ``provisional``: setup but no such stored score yet. Up to ``FEED_ON_REQUEST_CAP``
      candidates (``listings.service.candidate_set``, newest first) are scored now with the
      one scorer, filtered by the threshold rule, sorted in the feed order and cut to
      ``limit``; nothing is stored and ``next_cursor`` is null. When
      :func:`_needs_priority_mark`, it marks the Student stale on the ``priority`` queue
      (``feed_first_open``) and sets ``queued``.
    * ``hidden_below_threshold``: the toggle is off and a visible score (stored, or computed
      on request when provisional) is below the threshold.
    """
    after = decode_feed_cursor(cursor) if cursor else None
    skill_profile = await profile_service.get_skill_profile(session, owner_id)
    preferences = await profile_service.get_preferences(session, owner_id)
    threshold = (
        int(preferences.threshold)
        if preferences is not None and preferences.threshold is not None
        else DEFAULT_THRESHOLD
    )
    if not has_scoring_setup(skill_profile, preferences):
        return FeedPage("setup_needed", threshold, [], None)

    min_score = None if show_below_threshold else threshold
    rows = await repo.feed_page(
        session, owner_id, min_score=min_score, limit=limit + 1, after=after
    )
    if rows or after is not None or await repo.has_scores(session, owner_id):
        page = rows[:limit]
        entries = [
            FeedEntry(listings_service.BrowseItem(listing, label), value, value < threshold)
            for listing, label, value in page
        ]
        next_cursor = None
        if len(rows) > limit:
            last, _, last_score = page[-1]
            next_cursor = encode_feed_cursor(
                repo.FeedKey(last_score, last.posted_at, last.first_seen_at, last.id)
            )
        hidden = not show_below_threshold and await repo.has_scores(
            session, owner_id, below=threshold
        )
        return FeedPage("ready", threshold, entries, next_cursor, hidden_below_threshold=hidden)

    # Provisional: no stored score yet. Score the newest candidates now; store nothing.
    profile = build_profile(skill_profile, preferences)
    candidates = await listings_service.candidate_set(
        session,
        owner_id,
        role_titles=list(preferences.role_titles) if preferences else [],
        locations=list(preferences.locations) if preferences else [],
        job_types=list(preferences.job_types) if preferences else [],
        cap=get_settings().feed_on_request_cap,
    )
    weights = current_weights()
    scored = {
        c.listing_id: score(profile, _scoring_listing(c.scoring), weights)[0] for c in candidates
    }
    scores = {
        listing_id: value
        for listing_id, value in scored.items()
        if show_below_threshold or value >= threshold
    }
    hidden = not show_below_threshold and any(v < threshold for v in scored.values())
    items = await listings_service.visible_items(session, owner_id, scores)
    entries = sorted(
        (
            FeedEntry(item, scores[item.listing.id], scores[item.listing.id] < threshold)
            for item in items
        ),
        key=_feed_sort_key,
        reverse=True,
    )[:limit]

    queued = False
    if await _needs_priority_mark(session, owner_id):
        await mark_stale(session, owner_ids=[owner_id], reason=FEED_FIRST_OPEN, priority=True)
        queued = True
    return FeedPage(
        "provisional", threshold, entries, None, hidden_below_threshold=hidden, queued=queued
    )
