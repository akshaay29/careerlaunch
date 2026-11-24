# Epic 3 Context: A ranked, explained feed

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Turn the shared Listing pool into a personal, ranked feed. Every Student sees pool Listings, plus their own manual Listings later, scored 0–100 by a deterministic, explainable Match Score. The feed is filtered by their Preferences and sorted best fit first. Any score opens to show matched skills, missing skills and per-component points. Scores refresh within 5 minutes of a Resume, Skill Profile or Preferences change, and new Listings are scored in batches as they arrive. This is the product's core value: a new Student, with or without a Provider Key, reaches a first ranked feed within about 10 minutes of effort. The epic also feeds relevance back into `listings` for eviction, and it owns the scoring contract that later alerts and skill-gap insights will reuse.

## Stories

- Story 3.1: Deterministic Match Score with a breakdown
- Story 3.2: Keep my scores fresh
- Story 3.3: My ranked feed
- Story 3.4: See why a Listing scored what it did

## Requirements & Constraints

- **Determinism:**
  - The Match Score is an integer from 0 to 100 per Student–Listing pair.
  - It combines weighted skill overlap with text similarity between the Student's profile (target roles and skills) and the Listing's title and `search_doc`. No embeddings and no LLM are used.
  - Identical inputs always give an identical score and breakdown.
- **Explainability:** every score shown, stored or computed on request, opens to matched skills, missing skills, and each component's points out of its maximum. The component points sum to the score.
- **Freshness:** scores refresh within 5 minutes of a change to the active Resume, Skill Profile or Preferences (NFR, checked by an integration test with the drain). New pool Listings get scored for affected Students without per-Listing jobs.
- **Feed:**
  - Filtered by role, location and job type Preferences, and sorted by score descending. Ties sort by `posted_at` descending, then `first_seen_at`. Uses cursor pagination.
  - Expired Listings are always hidden. Listings below the Student's threshold are hidden by default, and one toggle, "Show below my threshold", reveals them.
  - A page loads in under 2 s at about 30,000 Listings.
- **Time to value:** a new Student with no stored scores still sees a ranked feed at once, because the top candidates are scored on request. A Provider Key is never required.
- **Metrics:** opening a breakdown records a count-only event, used to measure the share of Students who open breakdowns. Record no personal data.
- **Tone:** neutral and factual, with no nudges to apply more. Missing setup gives a neutral "finish setup" prompt with no pressure wording.
- **Observability:** every score computation run is logged and visible to the owner.

## Technical Decisions

- **One scorer:**
  - Only `matching` computes or writes scores, through the pure `matching.score(profile, listing, weights) -> (int 0..100, ScoreBreakdown)`. It does no I/O and uses no clock or randomness.
  - Feed, search, on-request scoring and future alerts all call this same function.
- **`ScoreBreakdown`:** a Pydantic model exposed in OpenAPI with `algo_version, weights_version, vocab_version, matched_skills[], missing_skills[], components[{name, points, max_points}]`.
- **Weights:**
  - Weights live in versioned config, and changing them bumps `weights_version`.
  - `weights_version` 1 sets α = 0.7 for skill overlap and 0.3 for text similarity, with equal per-skill weights.
  - Adding an embedding component later would need a new `algo_version`. It is out of scope now.
- **Skill vocabulary:**
  - Skills are compared as canonical `skill_id` slugs from `core.skills`.
  - If the profile and Listing skill sets have different `vocab_version`s, both are re-normalised through the alias map before comparison.
  - Custom skills outside the vocabulary are not scored in v1.
- **Tables (owned by `matching`):**
  - `match_scores(owner_id, listing_id, score, breakdown, algo_version, weights_version, vocab_version, computed_at)` is unique on `(owner_id, listing_id)` and has a check for 0–100.
  - `match_score_missing_skills(owner_id, listing_id, skill_id)`.
  - Both CASCADE from `accounts` and from `listings`.
  - The Student's `scores_stale_since` marker records when scores went stale.
- **Stored set:**
  - Scores are stored only for the candidate set: visible, unexpired pool Listings passing role, location and job type Preferences, capped at 500, plus the Student's manual Listings. Eligibility never excludes a Listing from the set.
  - Search results outside the set, and a new Student's feed before the first recompute, are scored on request and never stored.
- **Recompute:**
  - The only trigger is `matching.service.mark_stale(owner_id | listing_ids, reason)`, which is called from event subscribers and sets `scores_stale_since`.
  - Student-initiated changes enqueue `matching.recompute:student:<id>` on the `priority` queue, and the router starts an in-process bounded drain after the response.
  - The job upserts scores, deletes scores that dropped out of the set, and calls `listings.service.set_relevance(listing_id, max_score)`.
  - It runs again if `scores_stale_since` is newer than the job's start.
- **Events consumed:** `ResumeActivated`, `SkillProfileChanged`, `PreferencesChanged` and `ListingsIngested`. `ListingsIngested` is batched per ingestion run per affected Student. Subscribers run synchronously in the publisher's transaction. Only routers and tasks commit.
- **Cross-module reads:**
  - `matching.repo` may read `listings` (and `listing_criteria`) directly for the feed, as one indexed query joining `match_scores` to `listings`.
  - `listings.repo` may read `match_scores` for search ranking.
  - Everything else goes through service calls. `matching` sits right of `listings` and left of `tracker` in the import-linter module order.
- **Jobs:** arguments are ids only, keys follow `<module>.<task>:<entity_type>:<id>`, and there are no network calls inside a transaction. Free tiers only, so nothing waits on an always-on worker.

## UX & Interaction Patterns

- There is no UX document. Screens are defined by story behaviour.
- **Feed cards:** each shows the Match Score with an openable "Why this score" view, plus the source badge, the not-affiliated notice and the Apply link from Epic 2.
- **Threshold toggle:** a single control labelled "Show below my threshold".
- **Breakdown view:** shows matched skills, missing skills, and each component's points out of its maximum. It is available from the feed, the detail view and search results, including on-request scores.
- **Empty or incomplete setup:** a neutral prompt to finish the Skill Profile and Preferences. Scraped text is rendered as plain text.

## Cross-Story Dependencies

- 3.1 (pure `score()` and `ScoreBreakdown`) comes first. 3.2, 3.3 and 3.4 all call it.
- 3.2 creates `match_scores` and `match_score_missing_skills`, the stale/recompute flow and the ingestion batching. 3.3 reads the stored scores, and 3.4 reads stored breakdowns and on-request ones.
- 3.3 and 3.4 both depend on on-request scoring outside the candidate set, so share one code path.
- **From Epic 1:** `core.skills` (`normalize`, alias map, `vocab_version`), the Skill Profile with added/removed edits, Preferences (roles, locations, job types, threshold, time zone), the `ResumeActivated`, `SkillProfileChanged` and `PreferencesChanged` events, `core.jobs.enqueue`, the `priority` queue with its post-response drain, and `AccountDeleting`.
- **From Epic 2:**
  - The `listings` table with `search_doc`, `first_seen_at`, `posted_at`, expiry and visibility rules.
  - The `ListingsIngested` event and `listings.service.set_relevance()` (Story 2.5).
  - Search (2.6), which shows scores for its results.
- **For Epic 4:** manual Listings join the candidate set and are scored like any other. Tracker work must not compute scores itself.
