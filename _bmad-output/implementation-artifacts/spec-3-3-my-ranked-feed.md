---
title: 'Story 3.3: My ranked feed'
type: 'feature'
created: '2026-11-24'
status: 'done'
baseline_commit: '6f0b4bdb44873fc90d29a626fad3aab3961303b7'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-2-keep-my-scores-fresh.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Scores are stored and kept fresh, but a Student has nowhere to see Listings ranked by fit. A brand-new Student would see nothing until the first recompute (FR-10, NFR-1, NFR-4, UJ-1).

**Approach:**
- `GET /api/v1/feed` reads the Student's stored `match_scores` joined to visible, unexpired Listings, best first, with a threshold filter and a cursor.
- Before any scores exist, the top candidates are scored on request with the same `score()` and returned at once, unstored.
- A `/feed` page shows score badges, a "Show below my threshold" toggle and a neutral finish-setup prompt.

## Boundaries & Constraints

**Always:**
- **Endpoint:** `GET /api/v1/feed?cursor=&limit=&show_below_threshold=` (`operation_id` `getFeed`, matching router, `tags=["matching"]`). `limit` defaults to 20, max 50. The response is `Feed`:
  - `state`, one of:
    - `"ready"`: stored scores exist;
    - `"provisional"`: no stored scores yet, top candidates scored on request;
    - `"setup_needed"`: no scoring setup at all, using `has_scoring_setup` from 3.2.
  - `threshold` (the Student's Preferences threshold, default 60);
  - `items`: `ListingOut` fields plus `score` and `below_threshold: bool`. The breakdown is not included; 3.4 adds the breakdown view.
  - `next_cursor`.
- **Ready feed:**
  - One query: `match_scores` joined to `listings` and `sources` for this owner, where the Listing is visible to the owner, `expired_at IS NULL`, and `score >= threshold` unless `show_below_threshold`.
  - Order: `score DESC`, `posted_at DESC NULLS LAST`, `first_seen_at DESC`, `id DESC`.
  - Keyset cursor over those four values, encoded opaquely and validated like the browse cursor (an invalid cursor is a 422).
  - Expired or taken-down Listings never appear, even when a stale score row still references them. This settles the 3.2 deferral.
  - This is the one allowed cross-module read (AD-9). It lives in `matching.repo`, reads `listings` and `sources` directly, and uses the `(owner_id, score DESC)` index.
- **Provisional feed:**
  - When the Student has scoring setup but no stored scores, the endpoint takes the candidate set through `listings.service.candidate_set` (capped at the new config `FEED_ON_REQUEST_CAP`, default 60), scores each one with `matching.scoring.score`, applies the threshold rule, sorts with the same order, and returns the first `limit` items.
  - `next_cursor` is null and nothing is stored.
  - If no recompute is pending, it also calls `mark_stale([owner], reason="feed_first_open", priority=True)` and commits, and the router starts the after-response priority drain. The next load is then `ready`.
- **Setup needed:** `items` is empty, `next_cursor` is null, and no query runs against `match_scores`.
- **Performance:** `api/tests/test_feed_perf.py` seeds about 30,000 Listings and 500 stored scores for one Student. It asserts the first page, a deep page, and a page with the toggle on each take under 2 s (best of 3 after a warm-up). It also asserts the provisional path takes under 2 s with `FEED_ON_REQUEST_CAP` candidates.
- **Web:** a protected `/feed` page.
  - Add `FEED_PATH` to `PROTECTED_PREFIXES`.
  - Link it from the settings hub, the onboarding "You're set up" screen and `/listings`.
  - Cards reuse `ListingCard` (attribution, the not-affiliated notice, Apply), plus a score badge ("72 / 100") and a muted "Below your threshold" label when it applies.
  - A single checkbox labelled "Show below my threshold" lives in the `below=1` URL parameter.
  - "Load more" follows the cursor.
  - `provisional`: a neutral line, "Scores are being prepared. These are your top matches so far."
  - `setup_needed`: a neutral prompt, "Add your skills and job preferences to see Listings ranked for you.", with links to `/settings/skills` and `/settings/preferences`, and no urgency or pressure wording.
  - Empty `ready` feed (everything below the threshold): "No Listings meet your threshold yet. You can show the ones below it." with the toggle.
  - Pure helpers go in `web/src/lib/feed.ts` with `node --test` tests. Regenerate the client.
- Plain text for every scraped field. There is no "apply more" nudge anywhere.

**Never:**
- Storing provisional scores.
- A second scorer.
- Breakdown UI or the breakdown in the feed payload (3.4).
- Eligibility filtering or alerts.
- Changing the recompute rules.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Ready, ordered | stored scores 90, 75, 75 (posted newer/older), 60 | order 90, 75 (newer), 75 (older), 60; `state` `ready` | N/A |
| Tie on `posted_at` | same score, one `posted_at` NULL | NULL last; then `first_seen_at` desc, then id | N/A |
| Threshold default | threshold 60; scores 80, 59 | only 80; the 59 is hidden | N/A |
| Toggle | `show_below_threshold=true` | 80 and 59; 59 has `below_threshold: true` | N/A |
| Custom threshold | Preferences threshold 75 | scores under 75 hidden by default | N/A |
| Paging | 45 visible scores, `limit=20` | 20, 20, 5 with stable order across the keyset; `next_cursor` null at the end | invalid cursor: 422 |
| Expired | a scored Listing has expired | not in the feed | N/A |
| Taken down / private to another | score row exists but the Listing is gone or now private to someone else | not in the feed | N/A |
| Provisional | setup present, no stored scores | `state` `provisional`, items scored on request, sorted, at most `limit`, `next_cursor` null; one priority recompute queued | N/A |
| Provisional, nothing matches | setup present, no candidates | `provisional`, `items: []` | N/A |
| Setup needed | no Preferences filters, no skills | `setup_needed`, `items: []` | N/A |
| Then ready | after the drain | next load is `ready` with the same top Listings | N/A |
| Other Student | another Student's scores | never visible | 401 signed out |
| Perf | 30k Listings, 500 scores | each page under 2 s; provisional under 2 s | N/A |
| Web toggle | `/feed?below=1` | checkbox checked; below-threshold items labelled | N/A |
| Web setup prompt | `setup_needed` | neutral prompt with the two settings links | N/A |

</frozen-after-approval>

## Code Map

- `api/app/modules/matching/` -- `service.py` (`build_profile`, `scoring_profile`, `has_scoring_setup`, `mark_stale`, `recompute_key`), `scoring.py` (`score`), `repo.py` (match tables), `router.py` (`/listings/{id}/match` and its after-response drain pattern if any), `schemas.py`. Add the feed query to `repo.py`, `feed()` to `service.py`, `Feed` and `FeedItem` to `schemas.py`, and the route.
- `api/app/modules/listings/service.py` -- `candidate_set` (3.2), `encode_cursor`/`decode_cursor` (the browse cursor pattern), `ListingOut` mapping in `listings/router.py` (`_out`). Reuse the `ListingOut` shape for items, without duplicating mapping logic badly.
- `api/app/modules/profile/` -- the Preferences `threshold` (SmallInteger, default 60).
- `api/app/core/jobs.py` -- `drain_priority_after_response`; `api/app/modules/profile/router.py` shows how routers start it.
- `api/app/core/config.py` -- `FEED_ON_REQUEST_CAP`; `.env.example` and `render.yaml`.
- `web/src/app/listings/page.tsx`, `web/src/components/listings/listing-browser.tsx`, `listing-card.tsx`, `web/src/lib/listings.ts`, `web/src/lib/auth-paths.ts`, `web/src/app/settings/page.tsx`, `web/src/components/consent/onboarding-consent-step.tsx` (the done screen). Follow the existing Suspense, `searchParams` and Load-more patterns.
- Tests: `api/tests/test_feed.py` (every API matrix row), `test_feed_perf.py`, `web/src/lib/feed.test.ts`. Use the `match_fixtures.py` helpers from 3.2. Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] Config; `matching` feed query, service, schemas and route (ready, provisional, setup_needed).
- [x] Web `/feed` page, toggle, prompts, links, helpers and tests; `npm run gen:api`.
- [x] Tests: `test_feed.py`, `test_feed_perf.py`.

**Acceptance Criteria:**
- Given a new Student who has just saved skills and Preferences, when they open `/feed`, then they see a ranked `provisional` feed at once, and after the after-response drain the next load is `ready` with stored scores.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Provisional before ready.** Scoring 60 candidates in Python is fast. It is what makes UJ-1 ("a ranked feed at once") true without waiting for a job, and it triggers the job that makes later loads cheap.
- **The feed filters at read time.** Stored scores can lag expiry and takedown, so the join to `listings` with `expired_at IS NULL` and visibility is the guard, not the recompute.

## Implementation Notes

- `ListingOut.from_listing(listing, source_label, **extra)` is the one mapping; the listings router's `_out` and `FeedItem` (a `ListingOut` subclass) both use it.
- Ready query (`matching.repo.feed_page`): `posted_at DESC NULLS LAST` is keyed as `coalesce(posted_at, '-infinity')` so the keyset is one row comparison `(score, posted_key, first_seen_at, id) < cursor`. The cursor is base64 `score|posted_at or empty|first_seen_at|id`; a non-digit or out-of-range score, a naive timestamp, a bad UUID or the wrong number of parts is a 422.
- State: setup is checked first (`setup_needed` never touches `match_scores`). Otherwise the page query runs. The feed is `ready` when the page has rows, when a cursor was given (an exhausted cursor returns an empty `ready` page and never falls back to the provisional page 1), or when the Student has a stored score for a Listing they can still see (`repo.has_scores` joins `listings` with the same visibility and `expired_at IS NULL` guard as the page query). Otherwise it is `provisional`, so stored scores that only point at expired or hidden Listings do not pin the feed to an empty `ready`.
- `hidden_below_threshold` (on `Feed`): the toggle is off and at least one Listing the feed could show scores below the threshold (ready: one `EXISTS` over visible stored scores; provisional: any on-request score). The web shows "No Listings meet your threshold yet. You can show the ones below it." only when it is true, else "No Listings match your Preferences yet."
- Provisional: candidates come from `listings.service.candidate_set` with `cap=FEED_ON_REQUEST_CAP` (newest first); the display rows for the kept ids come from the new `listings.service.visible_items` (one query), then sort in Python with the same four keys (a minimum-datetime sentinel puts a NULL `posted_at` last).
- Priority mark rule (`service._needs_priority_mark`): the provisional path calls `mark_stale(..., reason="feed_first_open", priority=True)` unless (a) `last_computed_at` is set and not older than `scores_stale_since` (a recompute already ran after the last mark and stored nothing; marking again would only repeat it), or (b) `scores_stale_since` is younger than 15 minutes and the Student's priority recompute key is still pending (`core.jobs.job_pending`: a `job_outbox` row with that key, or a Procrastinate job with that queueing lock in `todo`). So a pending default-queue (ingest) job still gets a priority job, and a mark older than 15 minutes (its job may have failed) is made again; a duplicate priority job is folded by its queueing lock. The router then commits and starts `drain_priority` (a module global, as in the profile router, so tests can replace it).
- `getFeed` documents its 401 and 422 problem responses.
- `ListingCard` takes an optional `badge` slot; the feed passes the score badge (visible "72 / 100" plus an `sr-only` "Match Score" label) and the "Below your threshold" label.
- Feed view: the provisional notice has a "Refresh" button that reloads the first page; the toggle uses `router.replace`; async setState is guarded by a mounted ref; "Load more" reloads from the first page when the next page's `state` or `threshold` differs (`feedChanged`).
- The onboarding done screen now leads with "Open my feed" and keeps "Browse Listings"; the old "Your job feed will appear here once matching is available" copy is gone.

## Spec Change Log

- **2026-11-24, review pass 1 (finding 5):** the provisional feed scores the newest `FEED_ON_REQUEST_CAP` candidates, not the best ones overall, so "These are your top matches so far" overstated it and the matrix row "next load is `ready` with the same top Listings" holds only below the cap. Amended under the user's standing approval: the provisional line reads "Scores are being prepared. These are early matches from the newest Listings." and the ready feed may rank differently. KEEP: one feed query over `match_scores` joined to `listings`, the read-time visibility and expiry filter, the keyset order, the setup prompt wording.

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `has_scores` counts rows whose Listings are expired or no longer visible, so the feed is an empty `ready` forever instead of `provisional` | medium | `has_scores` reads `match_scores` alone | patch |
| 2 | A cursor whose stored scores vanished falls into the provisional branch and re-serves its first page; the client merges a different ranking | medium | the provisional branch ignores `after`; `loadMore` never checks `state` | patch |
| 3 | The first open queues no priority job when only a default-queue mark is pending, and a failed job's stale mark keeps the feed provisional forever | medium | `_recompute_pending_or_done` treats any `scores_stale_since` as pending | patch |
| 4 | The empty-state copy claims Listings are below the threshold when nothing was hidden | low | the API returns no hidden count | patch (add `hidden_below_threshold`) |
| 5 | "Your top matches so far" overstates a best-of-the-newest-60 ranking | low | `candidate_set` caps by newest | patch (spec amended) |
| 6 | Provisional never moves to ready without a reload; toggle adds history entries; setState after unmount; badge `aria-label` on a span is ignored | low | `feed-view.tsx` | patch |
| 7 | Tests missing: provisional order with mixed `posted_at` matches ready, no re-queue after an empty recompute, `FEED_ON_REQUEST_CAP` honoured; the 401 response is undocumented in OpenAPI | medium | verification-gap review | patch |
| 8 | Manual Listings escape the provisional cap | low | `candidate_set` adds every manual Listing | defer |
| 9 | Visibility and expiry predicates and the default threshold are duplicated across modules; `from_listing(**extra)` is untyped | low | duplication risk | defer |
| 10 | Perf test doesn't exercise stale rows, 5000 scores or HTTP serialisation | low | perf scope | defer |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
