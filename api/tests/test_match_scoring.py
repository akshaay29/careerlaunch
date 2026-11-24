"""FR-9, AD-8, AD-9: the pure, deterministic Match Score (Story 3.1). One test (or more) per
row of the I/O matrix, plus a Hypothesis property test for determinism and the sum rule."""

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core import skills
from app.modules.matching import weights as weights_module
from app.modules.matching.scoring import (
    ScoringListing,
    ScoringProfile,
    ENGLISH_STOP_WORDS,
    lexeme_coverage,
    query_terms,
    score,
    similarity,
    term_matches,
    title_similarity,
)
from app.modules.matching.weights import (
    ALGO_VERSION,
    CURRENT_WEIGHTS_VERSION,
    WEIGHTS,
    Weights,
    current_weights,
)

W = WEIGHTS[1]


def _profile(skill_ids=(), roles=(), vocab="2026.11.1") -> ScoringProfile:
    return ScoringProfile(frozenset(skill_ids), vocab, tuple(roles))


def _listing(title="Software Intern", skill_ids=(), lexemes=(), vocab="2026.11.1"):
    return ScoringListing(title, frozenset(skill_ids), vocab, frozenset(lexemes))


def _components(breakdown) -> dict[str, tuple[int, int]]:
    return {c.name: (c.points, c.max_points) for c in breakdown.components}


def _labels(refs) -> list[str]:
    return [r.label for r in refs]


def _check_sum_rule(value: int, breakdown) -> None:
    assert 0 <= value <= 100
    assert sum(c.points for c in breakdown.components) == value
    for c in breakdown.components:
        assert 0 <= c.points <= c.max_points
    assert sum(c.max_points for c in breakdown.components) == 100


# --------------------------------------------------------------------------- matrix rows


def test_full_skill_match() -> None:
    value, breakdown = score(
        _profile({"python", "django"}, ["Backend Developer Intern"]),
        _listing(
            "Backend Developer Intern",
            {"python", "django"},
            {"backend", "develop", "intern", "python", "django", "api"},
        ),
        W,
    )
    comps = _components(breakdown)
    assert comps["skills"] == (70, 70)
    assert comps["text"][1] == 30 and comps["text"][0] >= 27
    assert value >= 90
    assert _labels(breakdown.matched_skills) == ["Django", "Python"]
    assert breakdown.missing_skills == []
    _check_sum_rule(value, breakdown)


def test_partial_skills() -> None:
    value, breakdown = score(
        _profile({"python", "aws"}),
        _listing(skill_ids={"python", "django", "docker", "aws"}),
        W,
    )
    assert _components(breakdown)["skills"] == (35, 70)
    assert _labels(breakdown.matched_skills) == ["AWS", "Python"]
    assert _labels(breakdown.missing_skills) == ["Django", "Docker"]
    assert [r.id for r in breakdown.matched_skills] == ["aws", "python"]
    _check_sum_rule(value, breakdown)


def test_no_listing_skills_moves_weight_to_text() -> None:
    value, breakdown = score(
        _profile({"python"}, ["Data Analyst"]),
        _listing("Data Analyst", (), {"data", "analyst", "python"}),
        W,
    )
    comps = _components(breakdown)
    assert comps["skills"] == (0, 0)
    assert comps["text"][1] == 100
    # Identical title and every query term covered: a skill-less Listing can reach 100.
    assert value == 100
    _check_sum_rule(value, breakdown)


def test_empty_profile() -> None:
    value, breakdown = score(
        ScoringProfile(frozenset(), None, ()),
        _listing("Backend Intern", {"python", "docker"}, {"backend", "intern", "python"}),
        W,
    )
    assert value == 0
    assert [c.name for c in breakdown.components] == ["skills", "text"]
    assert _components(breakdown) == {"skills": (0, 70), "text": (0, 30)}
    assert breakdown.matched_skills == []
    assert _labels(breakdown.missing_skills) == ["Docker", "Python"]


def test_typo_ish_title() -> None:
    assert similarity("Backend Developer", "Back-end Developer Intern") > 0.4
    assert title_similarity(["Backend Developer"], "Back-end Developer Intern") > 0.4


def test_similarity_follows_pg_trgm() -> None:
    assert similarity("word", "word") == 1.0
    # pg_trgm: similarity('word', 'two words') = 0.363636 (4 shared of 11 trigrams).
    assert similarity("word", "two words") == pytest.approx(4 / 11)
    assert similarity("", "") == 0.0
    assert similarity("ABC", "abc") == 1.0
    assert title_similarity([], "anything") == 0.0


def test_stemmed_lexeme_is_covered() -> None:
    assert term_matches("engineering", "engin")
    assert lexeme_coverage(["engineering"], {"engin"}) == 1.0


def test_short_prefix_is_not_covered() -> None:
    assert not term_matches("go", "googl")
    assert lexeme_coverage(["go"], {"googl"}) == 0.0
    assert term_matches("go", "go")  # equal always matches


def test_only_a_lexeme_prefix_of_the_term_matches() -> None:
    # A stem prefixes its word; never the other way round, and never under 4 characters.
    assert term_matches("engineering", "engin")
    assert not term_matches("engin", "engineering")
    assert not term_matches("java", "javascript")
    assert not term_matches("react", "reaction")
    assert not term_matches("data", "dat")  # lexeme under 4 characters
    assert term_matches("data", "data")
    assert lexeme_coverage(["java"], {"javascript"}) == 0.0


def test_query_terms() -> None:
    terms = query_terms(["Back-end Developer", "Data Analyst"], {"nodejs", "cpp"})
    # "Node.js" -> node, js; "C++" -> c (too short); de-duplicated and sorted.
    assert terms == ["analyst", "back", "data", "developer", "end", "js", "node"]


def test_stop_words_are_not_query_terms() -> None:
    assert query_terms(["Head of Data and Analytics"], ()) == ["analytics", "data", "head"]
    assert "the" in ENGLISH_STOP_WORDS and len(ENGLISH_STOP_WORDS) == 127
    # Postgres drops "of" from search_doc, so the role can still reach full coverage.
    value, breakdown = score(
        _profile(roles=["Head of Data"]),
        _listing("Head of Data", (), {"head", "data"}),
        W,
    )
    assert value == 100
    _check_sum_rule(value, breakdown)


def test_vocab_drift_alias_ids_are_renormalised() -> None:
    # An old stored id that is an alias today maps to the current id on both sides.
    assert skills.lookup("golang") == "go"
    assert skills.lookup("postgres") == "postgresql"
    value, breakdown = score(
        _profile({"golang", "postgres"}, vocab="2025.01.0"),
        _listing(skill_ids={"go", "postgresql", "k8s"}, vocab="2026.11.2"),
        W,
    )
    assert [r.id for r in breakdown.matched_skills] == ["go", "postgresql"]
    assert [r.id for r in breakdown.missing_skills] == ["kubernetes"]
    assert breakdown.vocab_version == skills.VOCAB_VERSION
    _check_sum_rule(value, breakdown)


def test_unknown_skill_ids_compare_as_themselves() -> None:
    _, breakdown = score(
        _profile({"made-up-skill"}), _listing(skill_ids={"made-up-skill", "python"}), W
    )
    assert [r.id for r in breakdown.matched_skills] == ["made-up-skill"]
    assert breakdown.matched_skills[0].label == "made-up-skill"


def test_weights_versions() -> None:
    assert WEIGHTS[1] == Weights(version=1, skill=0.7, text=0.3)
    assert CURRENT_WEIGHTS_VERSION == 1 and current_weights() is WEIGHTS[1]
    assert ALGO_VERSION == 1
    with pytest.raises(KeyError):
        WEIGHTS[999]
    with pytest.raises(TypeError):
        weights_module.WEIGHTS[2] = Weights(version=2, skill=0.5, text=0.5)  # type: ignore[index]
    _, breakdown = score(_profile({"python"}), _listing(skill_ids={"python"}), W)
    assert breakdown.weights_version == 1
    assert breakdown.algo_version == 1


@pytest.mark.parametrize(
    ("skill", "text"),
    [(1.1, -0.1), (-0.1, 1.1), (0.7, 0.4), (0.5, 0.49), (float("nan"), 0.5)],
)
def test_invalid_weights_are_rejected(skill: float, text: float) -> None:
    with pytest.raises(ValueError):
        Weights(version=50, skill=skill, text=text)


def test_valid_weight_edges() -> None:
    assert Weights(version=51, skill=1.0, text=0.0).skill == 1.0
    assert Weights(version=52, skill=0.0, text=1.0).text == 1.0
    assert Weights(version=53, skill=0.1 + 0.2, text=0.7).skill == pytest.approx(0.3)


def test_largest_remainder_rounding() -> None:
    # 1 of 3 skills: 23.33 skill points; text: title similarity 0, coverage 1/2 -> 7.5.
    value, breakdown = score(
        _profile({"python"}, ["zzzz"]),
        _listing("Backend", {"python", "django", "docker"}, {"python"}),
        W,
    )
    # 23.333 + 7.5 = 30.83 -> 31; floors 23 + 7, the extra point goes to the larger
    # remainder (text, .5 > .33).
    assert value == 31
    assert _components(breakdown) == {"skills": (23, 70), "text": (8, 30)}


def test_remainder_ties_go_to_skills() -> None:
    equal = Weights(version=98, skill=0.5, text=0.5)
    value, breakdown = score(
        _profile({"python"}, ["qqqq"]),
        _listing("Backend", {"python", "django", "docker", "aws"}, {"python"}),
        equal,
    )
    # skills: 1/4 of 50 = 12.5; text: title 0, coverage 1/2 (qqqq, python) -> 0.25 x 50 = 12.5.
    # Total 25 -> floors 12 + 12; the remainders tie, so the extra point goes to skills.
    assert value == 25
    assert _components(breakdown) == {"skills": (13, 50), "text": (12, 50)}


# --------------------------------------------------------------------------- hash seeds

API_DIR = Path(__file__).resolve().parents[1]

# Scores multi-element sets (iteration order of str sets depends on the hash seed) and
# prints the score and breakdown as JSON.
_GOLDEN_SCRIPT = """
import json
from app.modules.matching.scoring import ScoringListing, ScoringProfile, score
from app.modules.matching.weights import WEIGHTS

profile = ScoringProfile(
    frozenset({"python", "golang", "postgres", "docker", "react", "aws", "sql"}),
    "2025.01.0",
    ("Backend Engineer", "Head of Data Platform", "Cloud Developer"),
)
listing = ScoringListing(
    "Backend Engineering Intern (Data Platform)",
    frozenset({"python", "go", "postgresql", "kubernetes", "kafka", "aws", "terraform"}),
    "2026.11.2",
    frozenset({"backend", "engin", "intern", "data", "platform", "python", "go", "aw", "cloud"}),
)
value, breakdown = score(profile, listing, WEIGHTS[1])
print(json.dumps({"score": value, "breakdown": breakdown.model_dump()}, sort_keys=True))
"""


# 4 of 7 Listing skills: 40.0 of 70; text 13 of 30.
GOLDEN_SCORE = 53
GOLDEN_COMPONENTS = [
    {"name": "skills", "points": 40, "max_points": 70},
    {"name": "text", "points": 13, "max_points": 30},
]


def _run_golden(seed: str) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run(
        [sys.executable, "-c", _GOLDEN_SCRIPT],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_golden_values_are_identical_across_hash_seeds() -> None:
    first, second = _run_golden("1"), _run_golden("12345")
    assert first == second
    breakdown = first["breakdown"]
    assert [s["id"] for s in breakdown["matched_skills"]] == ["aws", "go", "postgresql", "python"]
    assert [s["id"] for s in breakdown["missing_skills"]] == ["kafka", "kubernetes", "terraform"]
    assert first["score"] == GOLDEN_SCORE
    assert breakdown["components"] == GOLDEN_COMPONENTS
    assert sum(c["points"] for c in breakdown["components"]) == first["score"]


# --------------------------------------------------------------------------- property test

_SKILL_IDS = sorted(s.id for s in skills.all_skills())[:40] + ["golang", "postgres", "k8s", "zz-x"]
_WORDS = st.text(alphabet="abcdefgh-ij é", min_size=0, max_size=14)
_LEXEMES = st.text(alphabet="abcdefghij", min_size=1, max_size=10)


def _shuffled(values, rng: random.Random):
    items = list(values)
    rng.shuffle(items)
    return items


@settings(max_examples=300, deadline=None)
@given(
    profile_skills=st.lists(st.sampled_from(_SKILL_IDS), max_size=12),
    listing_skills=st.lists(st.sampled_from(_SKILL_IDS), max_size=12),
    roles=st.lists(_WORDS, max_size=4),
    title=_WORDS,
    lexemes=st.lists(_LEXEMES, max_size=20),
    weight=st.integers(min_value=0, max_value=100),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_property_deterministic_sum_rule_and_range(
    profile_skills, listing_skills, roles, title, lexemes, weight, seed
) -> None:
    w = Weights(version=7, skill=weight / 100, text=1 - weight / 100)
    first = score(
        ScoringProfile(frozenset(profile_skills), None, tuple(roles)),
        ScoringListing(title, frozenset(listing_skills), "v", frozenset(lexemes)),
        w,
    )
    value, breakdown = first
    _check_sum_rule(value, breakdown)
    rng = random.Random(seed)
    for _ in range(3):
        again = score(
            ScoringProfile(
                frozenset(_shuffled(profile_skills, rng)), None, tuple(roles)
            ),
            ScoringListing(
                title,
                frozenset(_shuffled(listing_skills, rng)),
                "v",
                frozenset(_shuffled(lexemes, rng)),
            ),
            w,
        )
        assert again[0] == value
        assert again[1].model_dump() == breakdown.model_dump()
    # Role-title order is the Student's list order; the best match does not depend on it.
    reordered = score(
        ScoringProfile(frozenset(profile_skills), None, tuple(_shuffled(roles, rng))),
        ScoringListing(title, frozenset(listing_skills), "v", frozenset(lexemes)),
        w,
    )
    assert reordered[0] == value
    assert reordered[1].model_dump() == breakdown.model_dump()
