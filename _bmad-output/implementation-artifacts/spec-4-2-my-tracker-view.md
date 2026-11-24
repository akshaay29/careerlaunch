---
title: 'Story 4.2: My Tracker view'
type: 'feature'
created: '2026-11-25'
status: 'done'
baseline_commit: '314c41065692e407286f61487593d9e335c22ec3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-1-save-a-listing-and-track-its-status.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Applications can only be opened one at a time from a Listing. There is no single place to see them all, filter them, or spot deadlines coming up, and cards don't show which Listings are already saved (FR-17).

**Approach:** Build a Tracker list API with combined filters and a total count, plus an "Upcoming deadlines" list.
- Deadlines come from the new `listings.service.effective_deadline` while the Listing exists, and from the snapshot otherwise.
- Stored Match Scores reuse the "Why this score" view.
- A batch saved-state lookup lets cards show "Saved · Open".
- The `/tracker` page puts it together.

## Boundaries & Constraints

**Always:**
- **`listings.service.effective_deadline(session, owner_id, listing_id) -> date | None`** and the batch `effective_deadlines(session, owner_id, listing_ids) -> dict[id, date | None]` (one query).
  - Phase 1 returns the Listing's `deadline` when the Listing is visible to the owner; there are no overrides yet. AD-7 adds override > extracted > source in phase 2, and the docstring says so.
  - A Listing that isn't visible to the owner is absent from the dict.
- **`GET /api/v1/applications`** (`listApplications`) returns `{items, total, next_cursor}`.
  - **Filters**, which combine with AND:
    - `status` can repeat (any of);
    - `company`: a case-insensitive substring of the snapshot company, trimmed, 1–100 characters;
    - `date_from` and `date_to` (inclusive, ISO dates) apply to `date_by`, which is `saved` (the `created_at` date in the Student's time zone, the default) or `applied` (`applied_on`; rows without one are excluded when a range is given). `date_from > date_to` is a 422.
  - `total` counts every row matching the filters, not just the page.
  - **Order:** `updated_at DESC, id DESC`, with an opaque keyset cursor (invalid gives a 422). `limit` defaults to 20, max 50.
  - **Each item:**
    - the 4.1 `ApplicationOut` fields;
    - `deadline`: the effective deadline when `listing_available`, else the snapshot deadline;
    - `in_pool`, the same as `listing_available`;
    - `score`: the Student's stored `match_scores.score` for that Listing when it exists and the Listing is available, else null. Scores are never computed here.
  - Loading is a fixed number of queries per page: one for the page, one for the count, one for availability and deadlines, and one for scores. Never one query per row.
  - `tracker` may read `match_scores` only through a `matching.service` function: `tracker` sits to the right of `matching`, so the import is allowed.
- **`GET /api/v1/applications/deadlines`** (`listUpcomingDeadlines`, `days` default 30, range 1–180) returns the Student's Applications in `saved`, `applied` or `interview` whose deadline (the same effective-or-snapshot rule) falls between today and today + `days` in the Student's time zone.
  - Sorted by deadline ascending, then title. At most 20.
  - Each item has `id`, `title`, `company`, `status`, `deadline`, `days_left` and `in_pool`.
- **`GET /api/v1/applications/by-listings?ids=…`** (`getApplicationsForListings`, at most 50 distinct ids, de-duplicated) returns `{items: [{listing_id, application_id}]}` for the Listings the Student has saved. This settles the 4.1 deferral on card state.
- Another Student's rows never appear; a single-Application route with someone else's id stays a 404 (4.1).
- **Web:**
  - **Protected `/tracker` page**, linked from the settings hub, `/feed` and `/listings`:
    - an "Upcoming deadlines" section at the top: title, company, deadline, "in N days" / "today", linking to `/tracker/[id]`; empty gives "No deadlines in the next 30 days.";
    - a filter bar with Status checkboxes, a company text box, from and to date inputs and a "saved / applied" selector, all kept in URL params, plus Clear;
    - "N Applications" (from `total`);
    - rows showing title (linking to `/tracker/[id]`), company, a Status chip, the deadline, a "No longer in the pool" marker when `in_pool` is false, and a score badge with "Why this score" (the 3.4 `MatchScoreBadge`) when `score` is not null;
    - Load more;
    - empty states: "No Applications yet. Save a Listing to start tracking it." when there are no rows at all, and "No Applications match these filters." when filters are applied.
  - **Cards (feed, search, browse):** after a page loads, the page calls `getApplicationsForListings` for its ids, and saved cards show "Saved · Open". Failures fall back to plain Save.
  - Pure helpers (filter parsing and serialising, days-left text, empty-state choice) go in `web/src/lib/tracker.ts` with tests. Regenerate the client.

**Never:**
- Computing scores in the tracker, or a second scorer.
- Deadline overrides or extraction (phase 2).
- Reminders or alerts.
- Manual Listings (4.3).
- Changing transitions.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| No filters | 25 Applications | first 20 by `updated_at` desc, `total` 25, cursor → 5 more | invalid cursor: 422 |
| Status filter | `status=applied&status=interview` | only those Statuses; `total` matches | unknown status: 422 |
| Company | `company=acme` | "Acme Corp" and "ACME" rows | over 100 characters: 422 |
| Combined | status + company + `date_from`/`date_to` (saved) | AND of all; `total` counts them | `date_from > date_to`: 422 |
| Applied dates | `date_by=applied` with a range | rows with `applied_on` in range; rows without `applied_on` excluded | N/A |
| Saved date time zone | saved at 23:30 IST | counts on that IST date, not the UTC date | N/A |
| Evicted Listing | Listing purged | row still listed from the snapshot; `in_pool` false; snapshot deadline | N/A |
| Expired / hidden Listing | Listing exists but expired or private to someone else | `in_pool` false; snapshot deadline; no score | N/A |
| Effective deadline | Listing in pool, deadline changed after save | the Listing's current deadline, not the snapshot | N/A |
| Score | stored `match_scores` row | `score` set; no stored row gives null | N/A |
| Deadlines | deadlines in 3, 10 and 40 days; one in the past; one on an Offer | days 3 and 10 (default 30), ascending; past and Offer excluded | `days` 0 or 181: 422 |
| Batch saved state | 20 ids, 3 saved | 3 items | more than 50 distinct: 422 |
| Other Student | their Applications | never listed | 401 signed out |
| Query count | 20 rows | fixed number of queries (asserted) | N/A |
| Web filters | change filters | URL updates; count and rows update; Clear resets | N/A |
| Web cards | saved Listing on the feed | "Saved · Open" links to `/tracker/[id]` | lookup fails: plain Save |

</frozen-after-approval>

## Code Map

- `api/app/modules/tracker/` (Story 4.1) -- `models.py`, `repo.py`, `service.py` (`ApplicationView`, `listing_available` via `listings_service.visible_items`, the `_today` time-zone helper), `schemas.py` (`ApplicationOut`), `router.py`. Add the list, deadlines and by-listings queries and routes. Declare `/applications/deadlines` and `/applications/by-listings` before `/applications/{id}`.
- `api/app/modules/listings/service.py` / `repo.py` -- `visible_items` / `_shown` visibility predicate; add `effective_deadline(s)`.
- `api/app/modules/matching/service.py` / `repo.py` -- add a small `stored_scores(session, owner_id, listing_ids) -> dict` (Story 3.4's `shown_scores` may already do this; reuse it).
- `api/app/modules/listings/service.py` `encode_cursor`/`decode_cursor` -- the cursor pattern.
- Web: `web/src/app/feed/page.tsx` and `listing-browser.tsx` (Suspense, `searchParams`, Load more), `web/src/components/tracker/*` (4.1), `web/src/components/matching/score-breakdown-view.tsx` (`MatchScoreBadge`), `listing-card.tsx` and `save-listing-button.tsx` (accept a known `applicationId`), `web/src/app/settings/page.tsx`.
- Tests: `api/tests/test_tracker_list.py` (every API matrix row, including a query-count assertion via a SQLAlchemy `before_cursor_execute` counter), `test_effective_deadline.py`. Reuse `tracker_fixtures.py`. Start the test Postgres with `timezone=UTC`; use 127.0.0.1. Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] `listings.effective_deadline(s)`; `matching` stored-scores lookup; `tracker` list, deadlines and by-listings service and routes.
- [x] Web `/tracker` page, filters, deadlines section, card saved state; helpers and tests; `npm run gen:api`.
- [x] Tests for every matrix row.

**Acceptance Criteria:**
- Given a Student with saved, applied and rejected Applications (one Listing purged, one with a stored score), when they open `/tracker` and filter by Status and company, then the count and rows match, the purged one is marked "No longer in the pool" with its snapshot deadline, the scored one opens "Why this score", and upcoming deadlines are listed by date.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Snapshot only when the Listing is gone.** AD-7 says the snapshot is read only when the Listing isn't available. "Available" uses the same visible-and-unexpired rule as 4.1's `listing_available`, so the list, detail and deadlines agree.
- **Stored scores only.** The Tracker shows what matching has already stored. A Listing outside the candidate set has no badge here, while the detail page can still score it on request.

## Implementation Notes

- **One query gives availability and deadlines.** `listings.repo.visible_deadlines` returns `{id: deadline}` for Listings visible to the owner and not expired, so the keys of `effective_deadlines` are also the "available" set. `tracker.service._view` now uses it too, so the save, get, PATCH and transition responses carry the effective deadline while the Listing is available (the snapshot column itself is never rewritten). An available Listing whose deadline is NULL falls back to the snapshot deadline, never wiping it.
- **Order and cursor:** `created_at DESC, id DESC` (see the Spec Change Log); the cursor keys on `(created_at, id)`, so a row whose Status changes between page loads appears exactly once.
- **Stored scores** come from `matching.service.stored_scores`, a thin wrapper over 3.4's `repo.shown_scores` (same visible-and-unexpired guard); nothing is scored.
- **Fixed query count per page:** preferences (time zone), page, count, closed-Status history (`allowed_next` for Rejected/Withdrawn rows, batched with `DISTINCT ON`), availability + deadlines, stored scores. `test_a_page_costs_a_fixed_number_of_queries` asserts a page with no closed rows stays within 6, and 3 rows and 25 rows (with closed rows) cost the same (at most 6).
- **Saved-date filter:** the inclusive local dates become UTC instants in Python (`zoneinfo` via `core.clock.zone`, unknown zones fall back to Asia/Kolkata): `created_at >= local midnight of date_from` and `created_at < local midnight of date_to + 1`. No zone name reaches Postgres. The company filter escapes LIKE wildcards; the route caps `company` at 100 characters, the same as the service (which also rejects an all-blank value). `by-listings` takes at most 200 raw `ids` and 50 distinct.
- **Upcoming deadlines:** SQL loads open (Saved, Applied, Interview) Applications that either still reference a Listing or whose snapshot deadline is inside [today, today + days] (today in the Student's zone), at most 500, newest saved first (`repo.deadline_candidates`). One `effective_deadlines` call resolves the rest; Python filters, sorts (deadline, title case-folded, id) and caps at 20, since `tracker` cannot join `listings`.
- **Web:** `/tracker` (`components/tracker/tracker-view.tsx`): the filter bar keeps a local draft that re-syncs whenever the URL changes (it is not remounted). Checkboxes and the saved/applied selector apply at once; the company and the dates apply on blur, Enter or Apply, through one apply path that skips a repeat of the URL just requested (no blur-plus-click double navigation). A range entered in either order is checked only on apply and stays as typed with a message; dates before 2000-01-01 are ignored (as for the applied date); the company length is counted in code points. The filters live in the URL (`status`, `company`, `from`, `to`, `by`). The list is remounted per filter set; deadlines load once, with one shared `DEADLINE_DAYS = 30` for the fetch and the empty message. Feed and browse/search pages call `getApplicationsForListings` per loaded page (in batches of 50) and pass `applicationId` to `ListingCard`. A full load (first page, refresh, retry, toggle or query change) replaces the saved-state map and drops lookups from older loads; Load more adds to it. Failures leave plain Save.

## Spec Change Log

- **2026-11-25, review pass 1 (finding 1):** ordering and the keyset on `updated_at DESC, id DESC` skipped rows whose `updated_at` changed between page loads (a Status change in another tab moved them ahead of the cursor). Amended under the user's standing approval: the list is ordered by `created_at DESC, id DESC` (immutable, newest saved first) and the cursor keys on it. The fixed per-page query budget is six statements (preferences, page, count, closed-status history, availability and deadlines, scores), not four. KEEP: combined filters and `total`, effective-or-snapshot deadlines, stored scores only, the batch saved-state lookup, the Upcoming deadlines section.

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Keyset on `updated_at` skips rows changed between page loads | medium | `list_page` cursor | patch (spec amended: order by `created_at`) |
| 2 | Saved-date filter uses Postgres `timezone(zone, ...)`; a zone Python accepts but Postgres doesn't gives a 500 | low | `repo._filtered` | patch (compute UTC bounds in Python) |
| 3 | `upcoming_deadlines` loads every open Application and one unbounded IN list | medium | `with_statuses` | patch (pre-filter snapshot-only rows by deadline in SQL and cap rows loaded) |
| 4 | A NULL Listing deadline overrides a non-null snapshot deadline | medium | `_deadline` | patch (Listing deadline, else snapshot) |
| 5 | Web filter bar: remount on every change drops focus; dates apply per keystroke; blur and click race; reversed ranges snap back; `0000-01-01` passes the client | medium | `tracker-view.tsx` `FilterBar key` | patch |
| 6 | Saved-state maps only grow and the feed lookup has no stale-load guard | low | `feed-view.tsx`, `listing-browser.tsx` | patch |
| 7 | Query limits differ from service limits (`company` 400 vs 100); the 30-day window is hard-coded twice; company length counted in three units | low | router, `lib/tracker.ts` | patch |
| 8 | Tests missing: list `allowed_next` after repeated closings, deadlines in a non-default time zone, zero-closed-rows query count, the effective deadline on save/PATCH/transition responses | medium | verification-gap and blind reviews | patch |
| 9 | Visibility for deadlines no longer joins `sources` | false | `listings.source` is a NOT NULL FK (RESTRICT), so every Listing has a Source | reject |
| 10 | No component or e2e tests for the tracker page and card wiring | medium | no web harness | defer |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with `timezone=UTC`, at 127.0.0.1, roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
