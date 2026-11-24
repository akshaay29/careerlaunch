"""matching.schemas: the Match Score breakdown (AD-9) and API DTOs."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.listings.schemas import ListingOut


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


class FeedItem(ListingOut):
    """One Listing of the ranked feed with its Match Score (0-100). ``below_threshold``: the
    score is under the Student's threshold (only shown with ``show_below_threshold``). Every
    text field is scraped, untrusted text: render it as plain text."""

    score: int = Field(ge=0, le=100)
    below_threshold: bool


class Feed(BaseModel):
    """My Listings ranked by Match Score, best first (ties: newest ``posted_at``, then newest
    ``first_seen_at``).

    ``state``:

    * ``ready``: from my stored scores. Pass ``next_cursor`` as ``cursor`` for the next page
      (null = done).
    * ``provisional``: no stored scores yet; my top candidates were scored just now (one page,
      ``next_cursor`` null), from the newest Listings. Stored scores are being prepared.
    * ``setup_needed``: no skills and no job Preferences yet, so nothing can be ranked
      (``items`` is empty).

    ``threshold``: my Preferences threshold (60 by default). Listings below it are left out
    unless ``show_below_threshold``. ``hidden_below_threshold``: the toggle is off and at
    least one Listing I could see here scores below the threshold."""

    state: Literal["ready", "provisional", "setup_needed"]
    threshold: int = Field(ge=0, le=100)
    hidden_below_threshold: bool
    items: list[FeedItem]
    next_cursor: str | None
