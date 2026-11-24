---
title: 'Story 3.4: See why a Listing scored what it did'
type: 'feature'
created: '2026-11-24'
status: 'done'
baseline_commit: 'c76a9b77812a998e8b72ed4acabef9a5b7e4da7f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-3-my-ranked-feed.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Students see a score but can't see why, and search results and Listing details show no score at all (FR-9, SM-4).

**Approach:** A "Why this score" view, opened from the feed, the Listing detail page and search results, shows matched skills, missing skills and each component's points out of its maximum. Stored scores are used where they exist; anything else is scored on request with the same `score()` and never stored. Opening the view records a count-only event.

## Boundaries & Constraints

**Always:**
- **Breakdown endpoint:** `GET /api/v1/listings/{listing_id}/match` (Story 3.1) now returns the Student's stored score and breakdown when a `match_scores` row exists for a visible, unexpired Listing. Otherwise it scores on request, as today. It gains `source: Literal["stored", "on_request"]`. Nothing is ever stored by this endpoint. The 404, 401 and 422 behaviour stays.
- **Batch scores for search results:** `GET /api/v1/matches?ids=<uuid>&ids=…` (`operation_id` `getMatchScores`, at most 50 ids, de-duplicated; more than 50, or a bad id, is a 422) returns `items: [{listing_id, score, source}]` for each id that is visible to the Student and not expired. Invisible or unknown ids are simply left out, not an error.
  - Stored scores come from one query; the rest are scored on request in one batch using the 3.2/3.1 scoring-input loaders (one query for all remaining ids), with one profile load.
  - No breakdowns are returned; the view fetches one breakdown when opened.
- **View event:** `POST /api/v1/listings/{listing_id}/match/viewed` (`operation_id` `recordBreakdownViewed`, 204) records `ops_events` kind `matching.breakdown_opened`, `owner_id` = the Student, `counts={"opened": 1}`. There is no listing id, score or skill in the event.
  - A Listing the Student can't see is a 404 and records nothing.
  - SM-4's "share of Students who open breakdowns" is a distinct count of `owner_id` over these events, which goes in the README's metrics note.
- **Web, a shared `ScoreBreakdownView` component (dialog or disclosure):**
  - Opened by a "Why this score" button. It fetches `getListingMatch`, then fires `recordBreakdownViewed` once per open (fire and forget, failures ignored).
  - It shows:
    - the score ("72 / 100");
    - each component as "Skills: 35 of 70 points" and "Title and keywords: 20 of 30 points";
    - "Skills you have" (matched) and "Skills this Listing asks for that you don't list" (missing) as chips;
    - "Scored just now" for `on_request`.
  - Empty lists show "None". An extra line explains a skills max of 0: "This Listing lists no skills, so the title and keywords count for the whole score."
  - Neutral tone, plain text. It is reachable with the keyboard, closes with Escape, and focus returns to the button.
- **Placement:**
  - **Feed cards (3.3):** a "Why this score" button next to the score badge.
  - **Listing detail page `/listings/[id]`:** the score badge plus the button, using `getListingMatch` on load.
  - **Search results on `/listings?q=`:** after a results page loads, the page calls `getMatchScores` for its ids, then shows a badge and button on each card that got a score. Cards without a score show none, with no error.
  - Browse (no query) is unchanged.
- **Pure helpers** go in `web/src/lib/breakdown.ts` (component labels, point lines, the empty-list text, the no-skills note) with `node --test` tests. Regenerate the client.

**Never:**
- Storing on-request scores.
- A second scorer.
- Personal data beyond `owner_id` in the event.
- Changing feed ordering or recompute rules.
- Eligibility or alerts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Stored score | `match_scores` row for (Student, L) | `/match` returns that score and breakdown, `source: "stored"` | N/A |
| On request | no stored row | computed, `source: "on_request"`, nothing written | N/A |
| Stored but Listing expired | stored row; L expired | 404 | N/A |
| Batch | 20 ids: 5 stored, 12 not, 3 invisible | 17 items (5 stored, 12 on request); 3 omitted | N/A |
| Batch limits | 51 ids; a malformed id | 422 problem+json | N/A |
| Batch duplicates | same id twice | one item | N/A |
| Batch empty | `ids` missing | `items: []` | N/A |
| Event | open the breakdown | one `matching.breakdown_opened` row with `owner_id` and `{"opened": 1}`; 204 | invisible Listing: 404, nothing recorded |
| Signed out | any of the three | 401 | N/A |
| Web: feed | click "Why this score" | view opens with matched and missing skills and components; one view event | N/A |
| Web: detail | `/listings/{id}` | score badge plus button | score fetch fails: no badge, page still renders |
| Web: search | `/listings?q=intern` | each scored card shows a badge and button | scores fetch fails: no badges, results still shown |
| Web: no-skills Listing | skills `max_points` 0 | the explanatory line is shown | N/A |
| Web: keyboard | Tab to the button, Enter, Escape | opens, closes, focus returns | N/A |

</frozen-after-approval>

## Code Map

- `api/app/modules/matching/` -- `router.py` (`getListingMatch`, `getFeed`, the drain pattern), `service.py` (`match_listing`, `scoring_profile`, `build_profile`), `repo.py` (`match_scores` access, the shared visible-and-unexpired predicate from 3.3), `schemas.py` (`ListingMatch`, `ScoreBreakdown`). Add `source`, the batch endpoint and the viewed endpoint.
- `api/app/modules/listings/service.py` -- `get_scoring_input` (one Listing) and the 3.2/3.3 batch loaders (`visible_items`, `candidate_set`'s scoring input). Add or reuse a batch "scoring inputs for these visible ids" in one query.
- `api/app/core/ops.py` -- `record_event` (`owner_id`, counts).
- `web/src/components/feed/feed-view.tsx`, `web/src/components/listings/listing-card.tsx` (the `badge` slot from 3.3), `listing-detail.tsx`, `listing-browser.tsx` (search results and Load more), `web/src/lib/feed.ts` (score badge text). Add `web/src/components/matching/score-breakdown-view.tsx` and `web/src/lib/breakdown.ts` (+ tests).
- Tests: `api/tests/test_match_endpoint.py` (extend: stored vs on request, expired stored), `api/tests/test_match_batch.py`, `api/tests/test_breakdown_event.py`. Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] API: `source` on `/match`, `getMatchScores`, `recordBreakdownViewed`.
- [x] Web: `ScoreBreakdownView`, placement in the feed, detail and search; helpers and tests; `npm run gen:api`.
- [x] Tests for every API matrix row; README note on SM-4.

**Acceptance Criteria:**
- Given a Student with stored scores, when they open "Why this score" on a feed card, then the view shows the stored breakdown and one count-only event is recorded. Given a search result outside their candidate set, when it renders, then it shows a score computed on request that is not stored.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **One batch call per search page.** Scoring 20 or so Listings in Python is cheap; one request keeps the search page light and avoids 20 parallel breakdown fetches. The full breakdown is fetched only when someone opens it.
- **`owner_id` in the event, nothing else.** SM-4 needs a distinct count of Students, and the existing ops events already carry `owner_id`. The event holds no Listing, score or skill.

## Implementation Notes

- Stored lookup: `matching.repo.shown_scores(owner, ids)` is one query joining `match_scores` to `listings` with the feed's `_shown` guard (visible, `expired_at IS NULL`), so a stale row of an expired or now-hidden Listing is ignored and `/match` falls through to `get_scoring_input`, which 404s.
- A stored breakdown that fails `ScoreBreakdown` validation, or whose component points do not sum to the stored score, is not served: `/match` scores on request instead (`source: "on_request"`); the batch applies the same check, so a badge always matches the dialog.
- Batch: `listings.service.scoring_inputs(actor, ids)` (new, one query via `repo.get_visible_scoring_inputs`) is the batch loader for the ids without a stored score; `get_scoring_input` and `candidate_set` now share its `_scoring_input` cleaner. The profile is loaded once, only when something is left to score. Items come back in request order. The raw list is capped at 200 values (`Query(max_length=200)`); after de-duplication the service allows at most 50 distinct ids (422 above that).
- Event: `record_breakdown_opened` checks visibility with `listings.service.get_listing` (404, nothing recorded) and records `matching.breakdown_opened` with `owner_id` and `{"opened": 1}` only; the router commits and returns 204.
- Web: `components/matching/score-breakdown-view.tsx` exports `ScoreBreakdownView` (a Radix Dialog: Escape closes, focus returns to the trigger; it fetches `getListingMatch` on every open and fires `recordBreakdownViewed` after a successful fetch, errors ignored) and `MatchScoreBadge` (badge + button, used by the feed, the detail page and search results). The badge text reuses `scoreBadge` from `lib/feed.ts`.
- Search: `ListingBrowser` calls `getMatchScores` once per loaded results page (first page, retry, each Load more) and merges the results into a by-id map; a failure leaves the cards without badges. Browse (no `q`) never calls it.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The batch serves stored rows without the `_stored_breakdown` check, so a search badge can differ from the dialog; the batch fixture stores `'{}'` as a valid stored breakdown | medium | `match_scores` vs `match_listing` | patch |
| 2 | No test for a valid stored breakdown whose points don't sum to the stored score | medium | only `'{}'` is tested | patch |
| 3 | The 50-id cap counts duplicates (`Query(max_length=50)`), contradicting the README; the post-dedup check is unreachable | low | router vs service | patch |
| 4 | The batch is never tested with stored rows for expired or hidden Listings | medium | the fixture has no stored rows for them | patch |
| 5 | Detail page fetches `/match` twice, fetches it even when the Listing fails to load, and compares ids case-sensitively | low | `listing-detail.tsx` | patch |
| 6 | The dialog's error state has no retry | low | `BreakdownBody` | patch |
| 7 | Search scores are never reset on a new query; late `loadMore`/`retry` results from an old query apply | medium | `listing-browser.tsx` | patch |
| 8 | Failed opens don't count toward SM-4 | low | event fires after a successful fetch | patch (document that only successful opens count) |
| 9 | `SkillList` mixes `skillLabels` and direct mapping | low | direct cleanup | patch |
| 10 | The view event has no rate limit, dedupe or retention; SM-4's denominator ("active Students") is undefined | low | metrics design | defer |
| 11 | No component or e2e tests for the web rows (keyboard, one event per open, failure paths, browse never calls the batch) | medium | no web component harness | defer |
| 12 | `matchScoreIds` silently caps at 50 | low | page size is 20 | reject |
| 13 | The 3.3 sprint-status change is in this diff | false | repo convention: mark the previous story done on the next branch | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
