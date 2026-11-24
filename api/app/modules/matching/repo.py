"""matching.repo: SQL for ``match_scores``, ``match_score_missing_skills`` and
``match_state`` only (AD-2)."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.matching.models import MatchScore, MatchScoreMissingSkill, MatchState

# ``clock_timestamp()``, not ``now()``: a mark made late in a long transaction must still be
# newer than the start of a recompute that began before it.
_MARK_STALE = text(
    """
    INSERT INTO match_state (owner_id, scores_stale_since)
    SELECT o, clock_timestamp() FROM unnest(CAST(:owner_ids AS uuid[])) AS o
    ON CONFLICT (owner_id) DO UPDATE SET scores_stale_since = EXCLUDED.scores_stale_since
    """
)


async def mark_stale(session: AsyncSession, owner_ids: Sequence[uuid.UUID]) -> None:
    """Upsert ``scores_stale_since`` for every owner in one statement."""
    await session.execute(_MARK_STALE, {"owner_ids": list(owner_ids)})


async def state_owner_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Every owner with a ``match_state`` row."""
    result = await session.execute(select(MatchState.owner_id).order_by(MatchState.owner_id))
    return list(result.scalars())


async def db_clock(session: AsyncSession) -> datetime:
    return (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def lock_state(session: AsyncSession, owner_id: uuid.UUID) -> MatchState | None:
    """The owner's ``match_state`` row, locked until the transaction ends (None: no row, e.g.
    the account was deleted)."""
    stmt = (
        select(MatchState)
        .where(MatchState.owner_id == owner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_state(session: AsyncSession, owner_id: uuid.UUID) -> MatchState | None:
    stmt = (
        select(MatchState)
        .where(MatchState.owner_id == owner_id)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_computed(
    session: AsyncSession, owner_id: uuid.UUID, *, computed_at: datetime, candidates: int
) -> None:
    await session.execute(
        MatchState.__table__.update()
        .where(MatchState.owner_id == owner_id)
        .values(last_computed_at=computed_at, last_candidates=candidates)
    )


async def owner_scores(session: AsyncSession, owner_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """``{listing_id: score}`` of the owner's stored scores."""
    result = await session.execute(
        select(MatchScore.listing_id, MatchScore.score).where(MatchScore.owner_id == owner_id)
    )
    return {listing_id: score for listing_id, score in result.all()}


@dataclass(frozen=True)
class ScoreRow:
    listing_id: uuid.UUID
    score: int
    breakdown: dict[str, Any]
    algo_version: int
    weights_version: int
    vocab_version: str
    missing_skills: tuple[str, ...]


# Batches keep each statement's bind parameters well under the driver's limit.
_BATCH = 500


def _chunks[T](items: Sequence[T], size: int = _BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def upsert_scores(
    session: AsyncSession, owner_id: uuid.UUID, rows: Sequence[ScoreRow], computed_at: datetime
) -> None:
    """Upsert ``rows`` and replace their missing-skill rows."""
    if not rows:
        return
    for chunk in _chunks(rows):
        stmt = insert(MatchScore).values(
            [
                {
                    "owner_id": owner_id,
                    "listing_id": r.listing_id,
                    "score": r.score,
                    "breakdown": r.breakdown,
                    "algo_version": r.algo_version,
                    "weights_version": r.weights_version,
                    "vocab_version": r.vocab_version,
                    "computed_at": computed_at,
                }
                for r in chunk
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MatchScore.owner_id, MatchScore.listing_id],
            set_={
                "score": stmt.excluded.score,
                "breakdown": stmt.excluded.breakdown,
                "algo_version": stmt.excluded.algo_version,
                "weights_version": stmt.excluded.weights_version,
                "vocab_version": stmt.excluded.vocab_version,
                "computed_at": stmt.excluded.computed_at,
            },
        )
        await session.execute(stmt)
        await session.execute(
            delete(MatchScoreMissingSkill).where(
                MatchScoreMissingSkill.owner_id == owner_id,
                MatchScoreMissingSkill.listing_id.in_([r.listing_id for r in chunk]),
            )
        )
    missing = [
        {"owner_id": owner_id, "listing_id": r.listing_id, "skill_id": skill_id}
        for r in rows
        for skill_id in r.missing_skills
    ]
    for chunk in _chunks(missing):
        await session.execute(insert(MatchScoreMissingSkill).values(list(chunk)))


async def delete_scores_except(
    session: AsyncSession, owner_id: uuid.UUID, keep: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """Delete the owner's scores for Listings not in ``keep`` (their missing-skill rows
    cascade); returns the Listing ids removed."""
    stmt = delete(MatchScore).where(MatchScore.owner_id == owner_id)
    if keep:
        stmt = stmt.where(MatchScore.listing_id.not_in(list(keep)))
    stmt = stmt.returning(MatchScore.listing_id)
    return list((await session.execute(stmt)).scalars())


async def max_scores(
    session: AsyncSession, listing_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """``{listing_id: max score over every Student}`` for the given Listings that still have
    a stored score (one aggregate query)."""
    if not listing_ids:
        return {}
    result = await session.execute(
        select(MatchScore.listing_id, func.max(MatchScore.score))
        .where(MatchScore.listing_id.in_(list(listing_ids)))
        .group_by(MatchScore.listing_id)
    )
    return {listing_id: int(score) for listing_id, score in result.all()}

