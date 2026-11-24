---
title: 'Story 3.1: Deterministic Match Score with a breakdown'
type: 'feature'
created: '2026-11-24'
status: 'done'
baseline_commit: '1164c1d451257958ee0ee6a4e1f529edb285c5f1'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Listings have no Match Score, so the feed can't be ranked and a Student can't see why a job fits (FR-9, AD-8, AD-9).

**Approach:**
- One pure, deterministic scorer in the `matching` module produces a 0–100 score and a `ScoreBreakdown`. It combines canonical-skill overlap with lexical similarity of the Student's target roles and skills against the Listing title and `search_doc` lexemes, with no embeddings, no LLM and no I/O.
- An on-request endpoint scores one Listing for the signed-in Student, which puts the breakdown into OpenAPI for the later stories. Storing scores comes in Story 3.2.

## Boundaries & Constraints

**Always:**
- **Pure core** (`app/modules/matching/scoring.py`): `score(profile: ScoringProfile, listing: ScoringListing, weights: Weights) -> tuple[int, ScoreBreakdown]`.
  - No I/O, clock, randomness, environment or global mutable state, and no dependence on set or dict iteration order: every collection is sorted before use.
  - Frozen dataclasses carry the inputs:
    - `ScoringProfile(skills: frozenset[str], vocab_version: str | None, role_titles: tuple[str, ...])`;
    - `ScoringListing(title: str, skills: frozenset[str], vocab_version: str, lexemes: frozenset[str])`, where `lexemes` are the Listing's `search_doc` lexemes (Postgres English stems) loaded by the caller.
- **Re-normalisation:** both skill sets are mapped through the current alias map (`core.skills.lookup(id) or id`) before comparing, whatever their `vocab_version`s. The breakdown's `vocab_version` is the current `core.skills.VOCAB_VERSION`.
- **Skill component** (max `round(100 × α_skill)`):
  - matched = listing skills ∩ profile skills; missing = listing skills − profile skills.
  - With equal per-skill weights, the ratio is |matched| / |listing skills|.
  - When the Listing has no skills, the skill component's `max_points` is 0 and its weight moves to the text component, so a Listing without extracted skills can still reach 100.
- **Text component** (max = the rest), a 0..1 ratio made of two equal halves, each computed in pure Python:
  - **Title similarity:** the best, over the Student's role titles, of the trigram similarity between role title and Listing title, in pg_trgm style. Lower-case both, pad each word with two leading spaces and one trailing space, take the set of 3-grams, and compute |A∩B| / |A∪B|.
  - **Lexeme coverage:** the share of the Student's query terms (role-title words and skill labels, lower-cased and split into words of 2 or more letters or digits, de-duplicated) that match a Listing lexeme. A term matches when the term equals the lexeme, or either starts with the other and the shorter is at least 4 characters. English stems are prefixes of their words ("engineering" and "engin").
  - With no role titles and no skills, the text component is 0.
- **Points:** each component's points are its ratio × its `max_points`. They are rounded with the largest-remainder method so the integer points sum exactly to the integer score, which is clamped to 0..100. Ties in remainders go to the skill component.
- **Weights** (`app/modules/matching/weights.py`): versioned and immutable. `WEIGHTS = {1: Weights(version=1, skill=0.7, text=0.3)}`, plus `CURRENT_WEIGHTS_VERSION = 1`. Any change adds a new version instead of editing one. `ALGO_VERSION = 1` changes whenever the formula changes.
- **`ScoreBreakdown`** (Pydantic, in `matching/schemas.py`, exposed in OpenAPI):
  - fields `algo_version: int`, `weights_version: int`, `vocab_version: str`, `matched_skills: list[ListingSkill-like {id, label}]`, `missing_skills: [...]`, `components: list[{name: Literal["skills", "text"], points: int, max_points: int}]`;
  - skills are sorted by label, and the points sum to the score.
- **Endpoint:** `GET /api/v1/listings/{listing_id}/match` (`operation_id` `getListingMatch`, matching router, `tags=["matching"]`) returns `{listing_id, score, breakdown}` for a Listing visible to the actor (browse rules: 404 otherwise).
  - The profile comes from `profile.service.effective_skills` plus the Skill Profile's `vocab_version` and the Preferences' `role_titles`. A Student with neither still gets a valid score.
  - The Listing's lexemes are read with `tsvector_to_array(search_doc)` through a new `listings.service` function, since `matching` may import `listings` and `profile` (AD-20 order).
  - Read-only; nothing is stored.
- **Determinism test:** a Hypothesis property test (add `hypothesis` as a dev dependency) shows that random inputs scored repeatedly, and with shuffled input order, give identical results, that the points always sum to the score, and that the score always stays within 0..100.

**Never:**
- Embeddings, an LLM, network calls or SQL inside `score()`.
- Storing scores, a `match_scores` table, recompute jobs or events (Story 3.2); feed UI (3.3); breakdown UI (3.4).
- Eligibility filtering (later), or changing ingest.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Full skill match | Listing skills {python, django}, profile has both, title equals role | skills 70/70, text close to 30; score ≥ 90 | N/A |
| Partial skills | Listing {python, django, docker, aws}, profile {python, aws} | skills 35/70; matched [AWS, Python], missing [Django, Docker] | N/A |
| No Listing skills | Listing skills empty | skills `max_points` 0; text `max_points` 100 | N/A |
| Empty profile | no skills, no role titles | score 0, components present, no matched skills | N/A |
| Typo-ish title | role "Backend Developer", title "Back-end Developer Intern" | title similarity above 0.4 | N/A |
| Stemmed lexeme | profile term "engineering", lexeme "engin" | counts as covered | N/A |
| Short prefix | term "go", lexeme "googl" | not covered (shorter under 4 characters and unequal) | N/A |
| Vocab drift | profile stores an old alias id that `lookup` maps to a current id | treated as the current id | N/A |
| Sum rule | any input | sum of component points equals the score; 0 ≤ score ≤ 100 | N/A |
| Determinism | same inputs ×N, shuffled order | identical score and breakdown | N/A |
| Weights versions | `WEIGHTS[1]` | skill 0.7, text 0.3; breakdown carries `weights_version` 1 and `algo_version` 1 | unknown version: `KeyError` |
| Endpoint | visible Listing | 200 with score and breakdown | another Student's private or expired Listing: 404; signed out: 401 |

</frozen-after-approval>

## Code Map

- `api/app/modules/matching/*` -- stubs today (`router.py` has an empty `APIRouter`, already mounted? check `app/main.py`). Add `scoring.py`, `weights.py`, `schemas.py`, `service.py` (load inputs and call `score`), `router.py`. Services never commit.
- `api/app/core/skills.py` -- `VOCAB_VERSION`, `lookup`, `label`, `sorted_ids`. Pure; do not change the vocabulary.
- `api/app/modules/profile/service.py` -- `get_skill_profile`, `effective_skills` (custom skills are never scored), `get_preferences` (`role_titles`).
- `api/app/modules/listings/service.py` / `repo.py` -- `get_listing` and `_visible_to` (browse visibility); add a small function returning the visible Listing's title, skills, `vocab_version` and `tsvector_to_array(search_doc)` lexemes.
- `api/pyproject.toml` -- `hypothesis` in the dev dependency group; the import-linter layers (`tracker > matching > listings > profile > accounts`) must keep passing.
- Tests: `api/tests/test_match_scoring.py` (pure, every matrix row, plus the property test) and `api/tests/test_match_endpoint.py`. Regenerate the web client (`cd web && npm run gen:api`); no web UI.

## Tasks & Acceptance

**Execution:**
- [x] `matching/weights.py`, `matching/scoring.py`, `matching/schemas.py`: the pure scorer and breakdown.
- [x] `listings` scoring-input loader; `matching/service.py` and `router.py`: the endpoint.
- [x] Tests (pure, property, endpoint); `hypothesis` dev dependency; `npm run gen:api`.

**Acceptance Criteria:**
- Given a seeded Listing and a Student with a Skill Profile and role titles, when they call `GET /api/v1/listings/{id}/match`, then they get a 0–100 score whose component points sum to it, with matched and missing skills by label.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Lexemes in, not SQL inside.** AD-9 asks for Postgres lexical similarity, but `score()` must be pure. So the caller loads the Listing's stemmed lexemes once, and matching against them is prefix-aware Python. Story 3.2's batch scorer can load lexemes for many Listings in one query and reuse the same function.
- **No-skill Listings aren't penalised.** Moving the skill weight to text keeps a well-titled Listing without extracted skills rankable, and the breakdown shows the shift through `max_points`.

## Implementation Notes

- `WEIGHTS` is a read-only `MappingProxyType` (unknown version: `KeyError`; assignment: `TypeError`); `current_weights()` returns `WEIGHTS[CURRENT_WEIGHTS_VERSION]`.
- The integer score is the round-half-up of the exact component sum; `apportion()` then hands out the leftover points by largest remainder (ties to skills). A unit never goes to a component with a zero remainder, so points never exceed `max_points`.
- Breakdown skills use a new `MatchSkill {id, label}` model in `matching/schemas.py` (same shape as `ListingSkill`) so the pure scorer does not import listings DTOs.
- `Weights.__post_init__` raises `ValueError` unless 0 <= skill <= 1, 0 <= text <= 1 and skill + text == 1 within 1e-9 (`SUM_TOLERANCE`); `text_max` is still `100 - skill_max`.
- Lexeme coverage follows the amended rule (Spec Change Log): a term matches a lexeme when they are equal, or the lexeme is a prefix of the term and at least 4 characters ("engin" / "engineering" yes; "java" / "javascript" and "react" / "reaction" no). Query terms drop the Postgres `english` stop words (`scoring.ENGLISH_STOP_WORDS`, the 127 words of `english.stop`, checked against `to_tsvector('english', ...)` on Postgres 17), so "Head of Data" can reach full coverage.
- NULL or empty array elements (skills, role titles, lexemes) are dropped by `matching.service.scoring_profile` / `match_listing` and `listings.service.get_scoring_input` before the scorer sees them. Preferences already reject NULL or blank role titles in the DB, so that path is covered by a unit test with stubbed loaders.
- Cross-process determinism: a golden-value test scores multi-element sets in subprocesses under `PYTHONHASHSEED` 1 and 12345 and compares the JSON.
- Listing loader: `listings.repo.get_visible_scoring_input` / `listings.service.get_scoring_input` (same visibility and expiry rules as `get_listing`; 404 otherwise).

## Spec Change Log

- **2026-11-24, review pass 1 (finding 3):** the lexeme-coverage rule "either starts with the other and the shorter is at least 4 characters" matched unrelated words ("java" / "javascript", "react" / "reaction"). Amended under the user's standing approval: a term matches a lexeme when they are equal, or the lexeme is a prefix of the term and at least 4 characters (a Postgres English stem is a prefix of its word: "engin" / "engineering"). Query terms also drop the English stop words that Postgres strips from `search_doc`. KEEP: pure scorer, largest-remainder points, weight shift for skill-less Listings, the property test.

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap; the verification-gap layer found no gaps):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `Weights` is never validated and `weights.text` is never read; skill > 1 gives a negative text max and a 500 | medium | `score()` derives `text_max = 100 - skill_max`; no `__post_init__` | patch |
| 2 | Role-title stop words ("of", "and") are query terms but never `search_doc` lexemes, so such roles can never reach full coverage | medium | Postgres English config strips stop words | patch |
| 3 | Prefix rule matches unrelated words ("java" / "javascript"), and a test enshrines it | medium | bidirectional prefix with a 4-character minimum | patch (spec amended, see Spec Change Log) |
| 4 | NULL elements in skill or role-title arrays raise in `sorted()` / `.lower()` (500) | low | arrays allow NULL elements; direct filter | patch |
| 5 | Determinism across processes (hash seed) is not tested | low | the property test runs in one process | patch (golden values under two `PYTHONHASHSEED`s) |
| 6 | Endpoint: no test for a taken-down Listing, a NULL `search_doc`, or a malformed id; extra blank lines | low | test file and style | patch |
| 7 | Coverage is divided by the Student's own term count (a broad profile scores lower on text) and matched skills also count in text | medium | `lexeme_coverage` denominator; skill labels in query terms | defer (the spec defines the terms; ranking within one Student is largely unchanged) |
| 8 | `vocab_version` inputs are not read by `score()` | low | kept for Story 3.2 storage | reject |
| 9 | Exact endpoint assertions depend on the shared ingest fixture | low | intended end-to-end check | reject |
| 10 | Lexeme matching is O(terms x lexemes) for batch scoring | low | one on-request score today | defer (Story 3.2) |
| 11 | Spec and sprint status differ; 2.6/epic-2 bookkeeping in this diff | false | repo convention: the previous story is marked done on the next branch | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean
