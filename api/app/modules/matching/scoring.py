"""matching.scoring: the one pure, deterministic Match Score (FR-9, AD-8, AD-9).

``score(profile, listing, weights) -> (0..100, ScoreBreakdown)`` combines

* **skills**: canonical-skill overlap, |matched| / |Listing skills| with equal per-skill
  weights, worth ``round(100 x weights.skill)`` points. A Listing without skills gives this
  component 0 points maximum and its weight moves to text, so it can still reach 100;
* **text**: the rest, half the best pg_trgm-style trigram similarity between one of the
  Student's role titles and the Listing title, half the share of the Student's query terms
  (role-title words and skill labels, minus English stop words) found among the Listing's
  ``search_doc`` lexemes (equal, or a stem of at least 4 characters that prefixes the term).

No I/O, clock, randomness, environment or mutable global state, and no reliance on set or
dict iteration order: every collection is sorted before use. Both skill sets are mapped
through the current alias map first, whatever their ``vocab_version``. The caller loads the
Listing's lexemes (Postgres English stems); matching them here is prefix-aware.

Changing this formula bumps :data:`~app.modules.matching.weights.ALGO_VERSION`.
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.core import skills as core_skills
from app.modules.matching.schemas import MatchSkill, ScoreBreakdown, ScoreComponent
from app.modules.matching.weights import ALGO_VERSION, Weights

MAX_SCORE = 100
# A lexeme matches a longer query term as its prefix only when it is at least this long.
MIN_PREFIX = 4
# Query terms are words of letters or digits at least this long.
MIN_TERM = 2

# Runs of letters or digits (Unicode-aware, no underscore): pg_trgm's word characters.
_WORD = re.compile(r"[^\W_]+")

# Postgres's ``english`` text search stop words (``english.stop``): never ``search_doc``
# lexemes, so they are not query terms either ("Head of Data" can reach full coverage).
ENGLISH_STOP_WORDS = frozenset(
    """
    i me my myself we our ours ourselves you your yours yourself yourselves he him his
    himself she her hers herself it its itself they them their theirs themselves what which
    who whom this that these those am is are was were be been being have has had having do
    does did doing a an the and but if or because as until while of at by for with about
    against between into through during before after above below to from up down in out on
    off over under again further then once here there when where why how all any both each
    few more most other some such no nor not only own same so than too very s t can will
    just don should now
    """.split()
)


@dataclass(frozen=True)
class ScoringProfile:
    """The Student's side: effective canonical skills (custom skills excluded), the Skill
    Profile's ``vocab_version`` (None without one) and the Preferences' role titles."""

    skills: frozenset[str]
    vocab_version: str | None
    role_titles: tuple[str, ...]


@dataclass(frozen=True)
class ScoringListing:
    """The Listing's side: its title, canonical skills, their ``vocab_version`` and its
    ``search_doc`` lexemes (``tsvector_to_array``), loaded by the caller."""

    title: str
    skills: frozenset[str]
    vocab_version: str
    lexemes: frozenset[str]


# --------------------------------------------------------------------------- helpers


def _canonical(ids: Iterable[str]) -> frozenset[str]:
    """Re-normalise ``ids`` through the current alias map (an old alias id becomes the
    current id; an unknown id stays itself)."""
    return frozenset(core_skills.lookup(i) or i for i in sorted(ids))


def _refs(ids: Iterable[str]) -> list[MatchSkill]:
    return [MatchSkill(id=i, label=core_skills.label(i)) for i in core_skills.sorted_ids(ids)]


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def trigrams(text: str) -> frozenset[str]:
    """pg_trgm's trigram set: each lower-cased word padded with two leading spaces and one
    trailing space, cut into 3-grams."""
    grams: set[str] = set()
    for word in _words(text):
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return frozenset(grams)


def similarity(a: str, b: str) -> float:
    """pg_trgm ``similarity(a, b)``: |A n B| / |A u B| of the trigram sets (0 when both are
    empty)."""
    ta, tb = trigrams(a), trigrams(b)
    union = len(ta | tb)
    return len(ta & tb) / union if union else 0.0


def title_similarity(role_titles: Iterable[str], title: str) -> float:
    """The best similarity between one of ``role_titles`` and ``title`` (0 with none)."""
    return max((similarity(role, title) for role in sorted(role_titles)), default=0.0)


def query_terms(role_titles: Iterable[str], skill_ids: Iterable[str]) -> list[str]:
    """Role-title words and skill-label words, lower-cased, at least 2 characters, not an
    English stop word, de-duplicated and sorted."""
    texts = [*sorted(role_titles), *(core_skills.label(i) for i in sorted(skill_ids))]
    return sorted(
        {
            w
            for text in texts
            for w in _words(text)
            if len(w) >= MIN_TERM and w not in ENGLISH_STOP_WORDS
        }
    )


def term_matches(term: str, lexeme: str) -> bool:
    """Equal, or the lexeme is a prefix of the term and has at least 4 characters (a
    Postgres English stem is a prefix of its word: "engin" / "engineering"). A term is never
    a prefix match for a longer lexeme ("java" does not match "javascript")."""
    return term == lexeme or (len(lexeme) >= MIN_PREFIX and term.startswith(lexeme))


def lexeme_coverage(terms: list[str], lexemes: Iterable[str]) -> float:
    """The share of ``terms`` that match one of ``lexemes`` (0 without terms)."""
    if not terms:
        return 0.0
    ordered = sorted(lexemes)
    covered = sum(1 for t in terms if any(term_matches(t, lx) for lx in ordered))
    return covered / len(terms)


def apportion(exact: list[float], total: int) -> list[int]:
    """Integer parts of ``exact`` summing to ``total`` (largest remainder; ties go to the
    earlier entry)."""
    floors = [math.floor(x) for x in exact]
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: max(0, total - sum(floors))]:
        floors[i] += 1
    return floors


# --------------------------------------------------------------------------- the scorer


def score(
    profile: ScoringProfile, listing: ScoringListing, weights: Weights
) -> tuple[int, ScoreBreakdown]:
    """The Match Score (0..100) of ``listing`` for ``profile`` and its breakdown, whose
    component points sum to the score. Pure and deterministic."""
    profile_skills = _canonical(profile.skills)
    listing_skills = _canonical(listing.skills)
    matched = listing_skills & profile_skills
    missing = listing_skills - profile_skills

    skill_max = round(MAX_SCORE * weights.skill) if listing_skills else 0
    text_max = MAX_SCORE - skill_max

    skill_ratio = len(matched) / len(listing_skills) if listing_skills else 0.0
    if profile.role_titles or profile_skills:
        title = title_similarity(profile.role_titles, listing.title)
        coverage = lexeme_coverage(
            query_terms(profile.role_titles, profile_skills), listing.lexemes
        )
        text_ratio = (title + coverage) / 2
    else:
        text_ratio = 0.0

    exact = [skill_ratio * skill_max, text_ratio * text_max]
    total = min(MAX_SCORE, max(0, math.floor(sum(exact) + 0.5)))
    skill_points, text_points = apportion(exact, total)

    breakdown = ScoreBreakdown(
        algo_version=ALGO_VERSION,
        weights_version=weights.version,
        vocab_version=core_skills.VOCAB_VERSION,
        matched_skills=_refs(matched),
        missing_skills=_refs(missing),
        components=[
            ScoreComponent(name="skills", points=skill_points, max_points=skill_max),
            ScoreComponent(name="text", points=text_points, max_points=text_max),
        ],
    )
    return total, breakdown
