"""Shared helpers for the stored Match Score tests (Story 3.2)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import skills
from app.modules.listings import service as listings_service
from app.modules.matching import service as matching_service
from app.ports.source_adapter import ListingDraft
from tests.listing_fixtures import ListingDb

RECOMPUTE = "matching.recompute"


class MatchDb(ListingDb):
    """Direct SQL for the matching tables (autocommit)."""

    def set_profile(
        self,
        owner_id: uuid.UUID,
        skill_ids: Sequence[str],
        *,
        added: Sequence[str] = (),
        removed: Sequence[str] = (),
    ) -> None:
        self.execute(
            "INSERT INTO app.skill_profiles (owner_id, status, extracted_skills, added_skills, "
            "removed_skills, vocab_version) VALUES (%s, 'ready', %s, %s, %s, %s) "
            "ON CONFLICT (owner_id) DO UPDATE SET extracted_skills = EXCLUDED.extracted_skills, "
            "added_skills = EXCLUDED.added_skills, removed_skills = EXCLUDED.removed_skills",
            (owner_id, list(skill_ids), list(added), list(removed), skills.VOCAB_VERSION),
        )

    def set_preferences(
        self,
        owner_id: uuid.UUID,
        roles: Sequence[str] = (),
        *,
        locations: Sequence[str] = (),
        job_types: Sequence[str] = (),
    ) -> None:
        self.execute(
            "INSERT INTO app.preferences (owner_id, role_titles, locations, job_types) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (owner_id) DO UPDATE SET "
            "role_titles = EXCLUDED.role_titles, locations = EXCLUDED.locations, "
            "job_types = EXCLUDED.job_types",
            (owner_id, list(roles), list(locations), list(job_types)),
        )

    def scores(self, owner_id: uuid.UUID) -> dict[uuid.UUID, int]:
        return dict(
            self.execute(
                "SELECT listing_id, score FROM app.match_scores WHERE owner_id = %s", (owner_id,)
            )
        )

    def score_rows(self, owner_id: uuid.UUID) -> list[tuple]:
        return self.execute(
            "SELECT listing_id, score, breakdown, algo_version, weights_version, vocab_version, "
            "computed_at FROM app.match_scores WHERE owner_id = %s ORDER BY listing_id",
            (owner_id,),
        )

    def missing(self, owner_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        return [
            (lid, sid)
            for lid, sid in self.execute(
                "SELECT listing_id, skill_id FROM app.match_score_missing_skills "
                "WHERE owner_id = %s ORDER BY listing_id, skill_id",
                (owner_id,),
            )
        ]

    def state(self, owner_id: uuid.UUID) -> tuple | None:
        rows = self.execute(
            "SELECT scores_stale_since, last_computed_at, last_candidates FROM app.match_state "
            "WHERE owner_id = %s",
            (owner_id,),
        )
        return rows[0] if rows else None

    def outbox(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [
            (key, queue, args)
            for key, queue, args in self.execute(
                "SELECT key, queue, args FROM app.job_outbox WHERE task = %s "
                "ORDER BY created_at, id",
                (RECOMPUTE,),
            )
        ]

    def clear_outbox(self) -> None:
        self.execute("DELETE FROM app.job_outbox")

    def ops(self, kind: str) -> list[tuple]:
        return self.execute(
            "SELECT owner_id, counts FROM app.ops_events WHERE kind = %s ORDER BY created_at, id",
            (kind,),
        )

    def now(self) -> datetime:
        """The database clock (the host clock may differ from the container's)."""
        return self.execute("SELECT clock_timestamp()")[0][0]

    def relevance(self, listing_id: uuid.UUID) -> float:
        return self.execute("SELECT relevance FROM app.listings WHERE id = %s", (listing_id,))[0][0]

    def id_of(self, source_job_id: str) -> uuid.UUID:
        return self.execute(
            "SELECT id FROM app.listings WHERE source_job_id = %s", (source_job_id,)
        )[0][0]

    def set_skills(self, listing_id: uuid.UUID, skill_ids: Sequence[str]) -> None:
        self.execute(
            "UPDATE app.listings SET skills = %s WHERE id = %s", (list(skill_ids), listing_id)
        )


async def ingest(
    engine: AsyncEngine, drafts: Sequence[ListingDraft], *, collected_by: uuid.UUID | None = None
) -> listings_service.IngestCounts:
    async with AsyncSession(engine) as session:
        counts = await listings_service.ingest_drafts(session, drafts, collected_by=collected_by)
        await session.commit()
    return counts


async def mark(
    engine: AsyncEngine, *owners: uuid.UUID, priority: bool = True, reason: str = "test"
) -> None:
    async with AsyncSession(engine) as session:
        await matching_service.mark_stale(
            session, owner_ids=owners, reason=reason, priority=priority
        )
        await session.commit()


async def recompute(owner: uuid.UUID, *, priority: bool = True) -> matching_service.RecomputeResult:
    return await matching_service.recompute_for_owner(owner, priority=priority)
