"""matching.schemas: the Match Score breakdown (AD-9) and API DTOs."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class MatchSkill(BaseModel):
    """A canonical skill in a breakdown."""

    id: str
    label: str


class ScoreComponent(BaseModel):
    """One part of the score: ``points`` out of ``max_points``. ``skills`` is the canonical
    skill overlap; ``text`` is the similarity of the Student's target roles and skills to the
    Listing's title and text. A Listing without skills gives ``skills`` a ``max_points`` of 0."""

    name: Literal["skills", "text"]
    points: int = Field(ge=0, le=100)
    max_points: int = Field(ge=0, le=100)


class ScoreBreakdown(BaseModel):
    """Why a Listing scored what it did. The components' ``points`` sum to the score. Skills
    are sorted by label: ``matched_skills`` are the Listing's skills the Student has,
    ``missing_skills`` the ones they do not."""

    algo_version: int
    weights_version: int
    vocab_version: str
    matched_skills: list[MatchSkill]
    missing_skills: list[MatchSkill]
    components: list[ScoreComponent]


class ListingMatch(BaseModel):
    """The Match Score (0-100) of one Listing for the signed-in Student, computed on request."""

    listing_id: uuid.UUID
    score: int = Field(ge=0, le=100)
    breakdown: ScoreBreakdown
