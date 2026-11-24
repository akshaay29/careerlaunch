---
title: 'Story 3.2: Keep my scores fresh'
type: 'feature'
created: '2026-11-24'
status: 'done'
baseline_commit: 'aa5964a51fd255872647543a228b813dd8d58d05'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-1-deterministic-match-score-with-a-breakdown.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.1 scores one Listing on request, but nothing stores scores or refreshes them when a Student's Resume, Skill Profile or Preferences change, or when new Listings arrive. The feed (3.3) needs stored, fresh scores (FR-9, NFR-3, AD-9).

**Approach:**
- `matching` stores each Student's scores for their candidate set.
- One trigger, `mark_stale`, is called from event subscribers and enqueues a per-Student recompute job: on the `priority` queue, drained right after the request, for the Student's own edits; on the default queue for new Listings.
- The job re-scores the candidate set with the Story 3.1 scorer, prunes scores that dropped out, and feeds `set_relevance`.

## Boundaries & Constraints

**Always:**
- **Migration `0017_match_scores`** (tables owned by `matching`, RLS through `create_app_table_rls`):
  - `match_scores(owner_id, listing_id, score smallint CHECK 0..100, breakdown jsonb, algo_version int, weights_version int, vocab_version text, computed_at timestamptz)`, with PK or UNIQUE `(owner_id, listing_id)`. FKs: `owner_id` → accounts ON DELETE CASCADE, `listing_id` → listings ON DELETE CASCADE. Indexes `(owner_id, score DESC)` and `(listing_id)`.
  - `match_score_missing_skills(owner_id, listing_id, skill_id)`, with PK on all three and a composite FK `(owner_id, listing_id)` → `match_scores` ON DELETE CASCADE.
  - `match_state(owner_id PK → accounts CASCADE, scores_stale_since timestamptz NULL, last_computed_at timestamptz NULL, last_candidates int NULL)`.
  - The owner-FK and listings-FK convention tests must pass.
- **`matching.service.mark_stale(session, *, owner_ids, reason, priority: bool)`:**
  - upserts `match_state.scores_stale_since = now()` for each owner;
  - enqueues `matching.recompute` with key `matching.recompute:student:<id>`, on the `priority` queue when `priority`, else `default`. The queueing lock drops duplicates while a job is pending.
  - It runs on the caller's session and never commits. `reason` (a short machine name) goes to the ops event.
- **Subscribers** (`matching/events.py`, registered at app wiring like `listings`):
  - `ResumeActivated`, `SkillProfileChanged` and `PreferencesChanged` call `mark_stale([owner], priority=True)`.
  - Every router that commits one of these events (profile upload, activate, edit, reset, Preferences save) must start the after-response priority drain (`core.jobs.drain_priority_after_response`); add it where it is missing.
- **`ListingsIngested`:**
  - Marks stale, on the default queue, every Student who has a `match_state` row or a Preferences row with role titles. The marking happens at most once per ingestion call: one bulk upsert and one enqueue per Student, never a job per Listing.
  - A Listing that is private to its collector (`visibility_owner_id`) marks only that collector.
- **Recompute job** (`matching.recompute`, ids `owner_id`; idempotent):
  1. Record `started = now()`.
  2. Build the candidate set in one query through a new `listings.service` function, limited to Listings visible to the Student and not expired:
     - **role:** the Listing's `title` matches at least one role title by full text (prefix tsquery from the same sanitised tokens as Story 2.6 search) or by trigram similarity at least `SEARCH_TRIGRAM_THRESHOLD`;
     - **location:** the Listing's location contains, case-insensitively, one of the Preferences locations;
     - **job type:** the Listing's `job_type` is in the Preferences, or is NULL;
     - an empty preference list means no filter on that field;
     - plus every Listing private to the Student (`source='manual'` and `visibility_owner_id = owner`);
     - capped at `MATCH_CANDIDATE_CAP` (config, default 500), newest `first_seen_at` first, private ones always kept.
     - It returns each Listing's title, skills, `vocab_version` and lexemes (the Story 3.1 scoring input) in the same query.
  3. Score each candidate with `matching.scoring.score`, current weights, the same profile builder as the endpoint.
  4. In one transaction:
     - upsert `match_scores` and replace `match_score_missing_skills` for the scored rows;
     - delete this owner's scores for Listings no longer in the set;
     - call `listings.service.set_relevance(session, listing_id, max score across all Students)` for every Listing whose scores changed or were removed (one aggregate query);
     - set `last_computed_at = started` and `last_candidates`;
     - record a counts-only `ops_events` row `matching.recompute` (`candidates`, `upserted`, `deleted`, `ms`).
  5. If `scores_stale_since > started` after the commit, enqueue itself again on the same queue.
  - A Student with no Preferences and no Skill Profile gets no candidates: all their scores are deleted and nothing fails.
  - Scoring runs in Python outside any long transaction: load in one short read transaction, score, then write.
- **Freshness integration test:** a Student saves a Skill Profile edit through the API, the after-response priority drain is run (as the tests do for other drains), and their `match_scores` reflect the new skills. The test asserts the scores changed and `computed_at > edit time`. It is the "within 5 minutes" proof: no polling, one drain.
- **Account deletion:** CASCADE removes the rows; nothing else is needed.

**Never:**
- One job per Listing; network calls; scoring inside SQL; a different scorer.
- Feed or breakdown UI or API (3.3, 3.4); eligibility filtering (later); alerts (later).
- Changing Story 3.1's `score()` maths.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Skill edit | `SkillProfileChanged` | `scores_stale_since` set; one priority job queued; after the drain, scores reflect the edit | N/A |
| Preferences save | role changed from "Backend" to "Data Analyst" | candidate set changes; old Backend-only scores deleted; Data Analyst Listings scored | N/A |
| Resume activated | `ResumeActivated` | marked stale, priority | N/A |
| Duplicate triggers | 3 edits before the drain | one pending job (queueing lock); scores reflect the last edit | N/A |
| Stale during run | `mark_stale` lands while the job runs | the job re-enqueues itself once it finishes | N/A |
| Ingest | 40 new pool Listings for 3 Students with Preferences | 3 default-queue jobs, not 40 or 120; new matching Listings scored | N/A |
| Private ingest | collector's private Listings | only the collector is marked | N/A |
| Visibility | another Student's private Listing matches the role | never in the candidate set | N/A |
| Expired | a scored Listing expires | removed from the set on the next recompute | N/A |
| Cap | 700 matching Listings | 500 newest scored; the Student's manual Listings always included | N/A |
| Empty profile | no Preferences, no Skill Profile | zero candidates; existing scores deleted; job succeeds | N/A |
| Relevance | Student A scores L at 80, Student B at 60 | `listings.relevance` for L = 80; after A's scores drop out, 60 | N/A |
| Missing skills | breakdown missing [docker, aws] | two `match_score_missing_skills` rows; replaced on the next recompute | N/A |
| Listing purged | Listing deleted | its `match_scores` and missing-skill rows cascade | N/A |
| Account deleted | owner removed | all their matching rows cascade | N/A |
| Idempotent | job runs twice with no change | same rows; no errors | N/A |
| Ops | any run | one counts-only `matching.recompute` event | N/A |

</frozen-after-approval>

## Code Map

- `api/app/modules/matching/` -- `scoring.py`, `weights.py`, `schemas.py`, `service.py` (`scoring_profile`, `match_listing`) from Story 3.1; add `models.py`, `repo.py`, `events.py`, `tasks.py`, `mark_stale` and `recompute_for_owner` in `service.py`. Services never commit; the task commits.
- `api/app/modules/listings/service.py` / `repo.py` -- `set_relevance` (Story 2.5), the search tokeniser and trigram schema helpers (`search_tokens`, `_trgm_schema`, `trigram_expressions` from Story 2.6), and `get_scoring_input` (3.1). Add the candidate-set query that returns scoring inputs.
- `api/app/modules/listings/events.py` and `app/main.py` -- how subscribers are registered at wiring.
- `api/app/core/jobs.py` -- `task(queue=...)`, `enqueue(..., queue=)`, `PRIORITY_QUEUE`, `drain_priority_after_response`. `api/app/modules/profile/router.py` -- the existing after-response drain on profile routes; check that the Preferences save route has one.
- `api/app/core/events.py` -- `ResumeActivated`, `SkillProfileChanged`, `PreferencesChanged`, `ListingsIngested`.
- `api/migrations/versions/0017_match_scores.py` -- after `0016_listing_search`; bump HEAD. Owner-FK and listing-FK convention tests.
- `api/app/core/config.py` -- `MATCH_CANDIDATE_CAP`; `.env.example` and `render.yaml`.
- Tests: `api/tests/test_match_recompute.py` (every matrix row), `test_match_freshness.py` (API edit, drain, scores). Follow `test_collection_schedule.py` and `test_listing_expiry.py` fixtures. Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] Config and migration 0017.
- [x] `matching` models, repo, `mark_stale`, subscribers, recompute task; `listings` candidate-set query; drains on the profile and Preferences routes.
- [x] Tests for every matrix row and the freshness integration test.

**Acceptance Criteria:**
- Given a Student with Preferences and a Skill Profile and a seeded pool, when they edit their skills through the API and the after-response drain runs, then their stored scores reflect the edit, the missing-skill rows match the breakdowns, and `listings.relevance` holds the max score per Listing.
- Given the full API suite, import-linter, the schema check, and the web suite, when they run, then everything passes.

## Design Notes

- **One job per Student, deduplicated by key.** Ingestion and edits both just mark stale and enqueue `matching.recompute:student:<id>`; the queueing lock folds bursts into one run, and the stale-since check catches changes that land mid-run.
- **Relevance is a pool-wide signal.** The max score across Students lets Story 2.5's eviction keep Listings someone cares about; it is recomputed only for Listings whose scores moved.

## Implementation Notes

- The after-response priority drain was already on every router that commits one of the four events (resume upload, activate, delete; skill-profile edit and reset; Preferences save), so no router changed.
- `matching.recompute` takes `owner_id` plus an int `priority` (1 or 0). It is used only so a run that must go again re-enqueues on the same queue and key. Keys follow the Spec Change Log: `matching.recompute:student:<id>:priority` for priority jobs and `matching.recompute:student:<id>` for default jobs.
- `scores_stale_since` and the job's start both use the database `clock_timestamp()`. The mid-run check compares the mark time, not the commit time. A mark written before the run started but committed after the run read its inputs is not caught by the check. The real safeguard is the job that the marking transaction queued itself: it commits with the change and is a new pending job, because the running job is no longer `todo` and so does not hold the queueing lock. The check only catches marks made while the run is in progress.
- Candidate set (`listings.service.candidate_set` / `repo.candidate_scoring_inputs`): the role match is a title-weighted prefix tsquery (`(backend:*A & developer:*A) | ...`) from `search_tokens`, or `word_similarity(role phrase, lower(title)) >= SEARCH_TRIGRAM_THRESHOLD`. Location uses `strpos(lower(location), lower(pref)) > 0`, so no LIKE escaping is needed.
- "Empty profile" means no Preferences filter (no role titles, locations or job types) and no effective skill. Such a Student gets zero candidates, so even their manual Listings are not scored. Otherwise, own manual Listings are added on top of the cap. Role titles that contain no word (e.g. `'` or `!`) match no pool Listing. A location filter keeps Listings whose location is unknown (empty). The manual predicate is NULL-safe, so a pool `manual` Listing goes through the filters.
- `ListingsIngested`: when one of the Listings is in the pool, the event marks Students with `match_state` rows, Students with any Preferences filter or effective skill (`profile.service.owner_ids_with_matching_setup`), and the collector. Otherwise it marks only the collector. Each mark writes one counts-only `matching.marked_stale` ops event.
- Write transaction (review pass 1):
  - Skips the write when `last_computed_at` is later than this run's start, so an older run never overwrites a newer one. It records `matching.recompute_superseded` instead.
  - Keeps only candidates that still exist, holding them `FOR KEY SHARE`.
  - Locks the changed Listings `FOR NO KEY UPDATE` in id order before computing the max for relevance.
  - `listings.repo.lock_listing` (pin/unpin/set_relevance) now takes `FOR NO KEY UPDATE` instead of `FOR UPDATE`. Only non-key columns change, and the weaker lock cannot deadlock against score writers that hold `FOR KEY SHARE`.
- `core.jobs.drain_priority_after_response` runs up to 3 rounds of the priority drain, within the existing 30 s. It starts another round while the previous one left new `priority` outbox rows, such as a recompute that re-enqueued itself or an extraction that published `SkillProfileChanged`.
- `MATCH_CANDIDATE_CAP` is 1 to 5000 (default 500).

## Spec Change Log

- **2026-11-24, review pass 1 (finding 2):** Procrastinate's queueing lock is global across queues, so with one key `matching.recompute:student:<id>` a Student's priority job was dropped whenever a default-queue job (from an ingest) was still pending, and their edit waited for the cron drain. Amended under the user's standing approval: priority jobs use the key `matching.recompute:student:<id>:priority`; default jobs keep `matching.recompute:student:<id>`. KEEP: mark_stale as the one trigger, candidate cap and visibility rules, the scorer, cascades.

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A candidate Listing deleted between the read and the write transaction violates `match_scores_listing_id_fkey` and the whole recompute fails | high | read, score, then write in a second transaction; only `_refresh_relevance` handles `NotFoundError` | patch |
| 2 | A priority job is dropped while a default job with the same key is pending (global queueing lock), so the edit waits for the cron drain | high | `flush_outbox` uses `queueing_lock=key` across queues | patch (spec amended) |
| 3 | A priority run that re-enqueues itself after the after-response drain flushed the outbox waits for the cron drain | medium | the drain flushes once, then runs the queue | patch (the drain repeats flush and run while it finds priority work, bounded) |
| 4 | An older run can commit after a newer one and overwrite fresher scores, moving `last_computed_at` backwards | medium | `lock_state` serialises writes only | patch (skip the write when `last_computed_at > started`) |
| 5 | Concurrent recomputes for different Students can leave `listings.relevance` at a stale max | medium | max read under read committed before the row lock | patch (lock Listing rows in sorted order first) |
| 6 | Students who existed before 0017 with skills or non-role Preferences are never marked by an ingest | medium | `on_listings_ingested` marks only `match_state` rows or role titles | patch (also mark owners with effective skills or any Preferences filter) |
| 7 | Listings with an empty location are excluded by a location filter, unlike NULL job types | medium | `strpos(lower(location), ...)` on '' | patch (keep empty-location Listings) |
| 8 | Role titles that yield no tokens disable the role filter and score the whole pool | medium | `if phrases` guard | patch (no role match possible means no pool candidates) |
| 9 | A pool manual Listing (`visibility_owner_id` NULL) is excluded by `~manual` (NULL logic) | low | `NOT(true AND NULL)` | patch |
| 10 | Tests missing: own manual Listing that also passes the filters (counted once), priority re-enqueue queue and args, an empty Preferences row, unsafe role characters, the `NotFoundError` relevance path, the Preferences-save and Resume-activate routes draining | medium | verification-gap and blind reviews | patch |
| 11 | The mid-run stale check compares mark time, not commit time | low | the separately queued outbox job is the real safeguard | patch (document it in Implementation Notes) |
| 12 | Nothing re-marks Students when the algorithm, weights or vocabulary version changes | medium | no version-change trigger | defer |
| 13 | Expiry and takedown don't refresh stored scores | low | the feed (3.3) must filter `expired_at` | defer |
| 14 | Every pool ingest recomputes almost every Student; unchanged rows are rewritten; the trigram role filter can't use an index; the `upserted` count always equals `candidates` | medium | cost at scale | defer |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run lint && npm run typecheck && npm test` -- expected: clean (no web change expected)
