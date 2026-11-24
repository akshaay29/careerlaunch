"""matching.service: loads the scoring inputs and calls the pure scorer (AD-9).

Story 3.1 scores one Listing on request and stores nothing. Services never commit.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.listings import service as listings_service
from app.modules.matching.schemas import ScoreBreakdown
from app.modules.matching.scoring import ScoringListing, ScoringProfile, score
from app.modules.matching.weights import current_weights
from app.modules.profile import service as profile_service


@dataclass(frozen=True)
class MatchResult:
    listing_id: uuid.UUID
    score: int
    breakdown: ScoreBreakdown


async def scoring_profile(session: AsyncSession, owner_id: uuid.UUID) -> ScoringProfile:
    """The Student's effective skills (custom skills never scored), the Skill Profile's
    ``vocab_version`` and the Preferences' role titles. Either may be missing."""
    skill_profile = await profile_service.get_skill_profile(session, owner_id)
    preferences = await profile_service.get_preferences(session, owner_id)
    # Array elements may be NULL; drop them (and empty strings) so the scorer never sees one.
    skills = {s for s in profile_service.effective_skills(skill_profile) if s}
    roles = preferences.role_titles if preferences is not None else []
    return ScoringProfile(
        skills=frozenset(skills),
        vocab_version=skill_profile.vocab_version if skill_profile is not None else None,
        role_titles=tuple(r for r in roles or [] if r and r.strip()),
    )


async def match_listing(
    session: AsyncSession, owner_id: uuid.UUID, listing_id: uuid.UUID
) -> MatchResult:
    """Score one Listing visible to ``owner_id`` (``NotFoundError`` otherwise). Read-only."""
    listing = await listings_service.get_scoring_input(session, owner_id, listing_id)
    profile = await scoring_profile(session, owner_id)
    value, breakdown = score(
        profile,
        ScoringListing(
            title=listing.title,
            skills=frozenset(s for s in listing.skills if s),
            vocab_version=listing.vocab_version,
            lexemes=frozenset(lx for lx in listing.lexemes if lx),
        ),
        current_weights(),
    )
    return MatchResult(listing_id, value, breakdown)
