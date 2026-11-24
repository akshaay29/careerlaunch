"""matching.models: ``match_scores``, ``match_score_missing_skills`` and ``match_state``
(DDL in migration 0017_match_scores).

Foreign keys are declared in the migration (AD-2: no ORM link into another module's table).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MatchScore(Base):
    """A stored Match Score of one candidate Listing for one Student (Story 3.2)."""

    __tablename__ = "match_scores"

    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    score: Mapped[int] = mapped_column(SmallInteger)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    algo_version: Mapped[int] = mapped_column(Integer)
    weights_version: Mapped[int] = mapped_column(Integer)
    vocab_version: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MatchScoreMissingSkill(Base):
    """One missing skill of a stored score's breakdown."""

    __tablename__ = "match_score_missing_skills"

    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    skill_id: Mapped[str] = mapped_column(Text, primary_key=True)


class MatchState(Base):
    """A Student's score freshness: when they went stale and when the last recompute (which
    started at ``last_computed_at``) found ``last_candidates`` candidates."""

    __tablename__ = "match_state"

    owner_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    scores_stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_candidates: Mapped[int | None] = mapped_column(Integer)
