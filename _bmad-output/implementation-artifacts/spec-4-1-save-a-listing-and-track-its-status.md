---
title: 'Story 4.1: Save a Listing and track its Status'
type: 'feature'
created: '2026-11-24'
status: 'done'
baseline_commit: '9463a58a58ec2d4edea3138984705ee9ef781f6a'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Student can find and rank Listings but has no way to save one and track where each application stands (FR-16, AD-7, AD-20).

**Approach:** The `tracker` module owns Applications. Saving a visible Listing creates an Application in Status Saved with a snapshot and pins the Listing. Status changes go only through `tracker.service.transition()`, which applies an explicit transition table and writes history in the same transaction. Notes, applied date and Resume version can be edited, and deleting an Application unpins the Listing.

## Boundaries & Constraints

**Always:**
- **Migration `0018_applications`** (tables owned by `tracker`, RLS):
  - **`applications`:**
    - `id`, `owner_id` → accounts ON DELETE CASCADE, `listing_id` → listings ON DELETE SET NULL, `status` (CHECK in `saved`, `applied`, `interview`, `offer`, `rejected`, `withdrawn`), `notes` text (≤ 4,000 characters), `applied_on` date NULL, `resume_id` → resumes ON DELETE SET NULL.
    - Snapshot columns `title`, `company`, `url` and `deadline` (date NULL), filled from the Listing at save.
    - `created_at` and `updated_at`.
    - A partial UNIQUE `(owner_id, listing_id) WHERE listing_id IS NOT NULL`, so a Listing can be saved once per Student.
    - Index `(owner_id, updated_at DESC)`.
  - **`application_status_history`:** `id`, `application_id` → applications ON DELETE CASCADE, `owner_id` → accounts CASCADE, `from_status` NULL, `to_status`, `changed_at`. Index `(application_id, changed_at DESC)`.
  - The owner-FK and listings-FK convention tests must pass. `applications.listing_id` is SET NULL as AD-7 requires.
- **Events:** add `ApplicationClosed(owner_id, application_id, listing_id | None)` and `ApplicationReopened(...)` to `core.events`. No subscribers yet; outreach will use them later.
- **`tracker.service` (services never commit):**
  - **`save_listing(session, owner_id, listing_id)`:**
    - The Listing must be visible to the Student and not expired (`listings.service.get_listing`). Otherwise `NotFoundError` (404).
    - If an Application already exists for that Listing, return it unchanged and do not pin again.
    - Otherwise:
      - insert it with status `saved` and the snapshot;
      - write a history row (`from_status` NULL → `saved`);
      - call `listings.service.pin(session, listing_id)`;
      - all in the caller's transaction.
  - **`transition(session, owner_id, application_id, to_status)`** is the only writer of `status`. Allowed moves:
    - `saved` → `applied` or `withdrawn`;
    - `applied` → `interview`, `rejected` or `withdrawn`;
    - `interview` → `offer`, `rejected` or `withdrawn`;
    - `rejected` or `withdrawn` → the status it was in just before closing (read from the latest history row whose `to_status` is the closed status), and only that one;
    - `offer` is terminal in this story.
  - A disallowed move raises a new `InvalidTransitionError` (409 problem+json, type `/problems/invalid-transition`) whose payload lists `allowed: [...]` (the allowed next Statuses).
  - Moving to the same status is a 409 too.
  - Each transition writes a history row in the same transaction and bumps `updated_at`.
  - Entering `rejected` or `withdrawn` publishes `ApplicationClosed`. Reopening publishes `ApplicationReopened`.
  - Moving to `applied` sets `applied_on` to today, in the Student's Preferences time zone (default Asia/Kolkata), if it is empty.
  - The Application row is locked (`FOR UPDATE`) so concurrent transitions serialise.
  - **`update_details(session, owner_id, application_id, *, notes?, applied_on?, resume_id?)`:** a partial update.
    - `notes` is trimmed, empty becomes NULL, and more than 4,000 characters is a 422.
    - `applied_on` may not be in the future (422).
    - `resume_id` must be one of the Student's own Resumes (422 otherwise), and null clears it.
    - It never changes `status`.
  - **`delete_application(session, owner_id, application_id)`:** deletes the row (the history cascades) and calls `listings.service.unpin` when `listing_id` is not NULL and the Listing still exists.
  - **`get_application(...)`** and **`history(...)`** return history newest first.
  - Another Student's Application is always a 404.
- **API** (tracker router, `tags=["tracker"]`; routers commit):
  - `POST /api/v1/applications {listing_id}` returns 201 when created and 200 when it already existed (`saveListing`);
  - `GET /api/v1/applications/{id}` (`getApplication`);
  - `PATCH /api/v1/applications/{id}` (`updateApplication`);
  - `POST /api/v1/applications/{id}/status {status}` (`transitionApplication`);
  - `DELETE /api/v1/applications/{id}` returns 204 (`deleteApplication`);
  - `GET /api/v1/applications/{id}/history` (`getApplicationHistory`);
  - `GET /api/v1/applications/by-listing/{listing_id}` returns `{application_id | null}` so a card can show "Saved" (`getApplicationForListing`).
  - `ApplicationOut` includes the snapshot fields; `listing_available` is false when `listing_id` is NULL; `allowed_next` lists the next Statuses the table allows.
- **Web:**
  - A "Save" button on feed cards, search and browse cards, and the Listing detail page. After saving it becomes "Saved · Open" and links to `/tracker/[id]`.
  - The detail page asks `getApplicationForListing` for its state. Cards just call save and show the result; save is idempotent.
  - A protected `/tracker/[id]` page shows:
    - the snapshot (title, company, deadline, an Apply link to `url`) and "This Listing is no longer available" when `listing_available` is false;
    - the current Status, with buttons for the `allowed_next` Statuses only (plain labels: "Mark as applied", "Interview", "Offer", "Rejected", "Withdraw", "Reopen");
    - a notes textarea with Save;
    - an applied date input;
    - a Resume picker listing the Student's Resumes, plus "None";
    - a history list (Status and timestamp, newest first);
    - a Delete button with a confirm dialog that returns to `/listings`.
  - Add `/tracker` to the protected prefixes.
  - Pure helpers (status labels, allowed-next labels, history formatting) go in `web/src/lib/tracker.ts` with tests. Regenerate the client.

**Never:**
- Writing `status` outside `transition()`.
- The Tracker list view or filters (4.2); manual Listings (4.3).
- Outreach or follow-up behaviour; reminders.
- Changing scoring or feed ordering.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Save | visible Listing | 201; status `saved`; snapshot stored; history row; `pin_count` +1 | N/A |
| Save twice | same Listing | 200, the same Application; `pin_count` unchanged | N/A |
| Save invisible | expired, or another Student's private Listing | 404; nothing written | N/A |
| Valid transitions | saved→applied→interview→offer | each allowed; history rows; `applied_on` set on `applied` if empty | N/A |
| Invalid | saved→offer | 409 `/problems/invalid-transition` with `allowed: ["applied","withdrawn"]` | N/A |
| Same status | applied→applied | 409 | N/A |
| Close | applied→rejected | `ApplicationClosed` published; history row | N/A |
| Reopen | rejected (was applied) → applied | allowed; `ApplicationReopened`; reopen to anything else is 409 | N/A |
| Offer terminal | offer→anything | 409 with `allowed: []` | N/A |
| Concurrent | two transitions at once | serialised; both history rows consistent | N/A |
| Details | notes, `applied_on`, `resume_id` | saved | future date, over-long notes, or another Student's Resume: 422 |
| Clear Resume | `resume_id: null` | cleared | N/A |
| Delete | an Application | 204; history gone; `unpin` called | N/A |
| Listing purged | Listing deleted later | `listing_id` NULL; snapshot shown; `listing_available` false; delete skips unpin | N/A |
| Resume deleted | the chosen Resume deleted | `resume_id` NULL | N/A |
| History | 4 changes | newest first with timestamps | N/A |
| Other Student | someone else's Application id | 404 on every route | 401 signed out |
| By listing | a saved Listing | `{application_id}`; an unsaved one gives `null` | N/A |

</frozen-after-approval>

## Code Map

- `api/app/modules/tracker/*` -- stubs today; add models, repo, service, schemas, router (mounted? check `app/main.py`), events. Import-linter order: `tracker` may import `matching`, `listings`, `profile`, `accounts`.
- `api/app/modules/listings/service.py` -- `get_listing` (visible and unexpired, else `NotFoundError`), `pin`, `unpin`.
- `api/app/modules/profile/service.py` / `models.py` -- Resumes (ownership check: add a small service function if none exists), Preferences time zone (`core.clock.local_day`).
- `api/app/core/events.py`, `api/app/core/errors.py` -- add the events and `InvalidTransitionError` (problem+json with `allowed` in `extra`, like `ConsentRequiredError`).
- `api/migrations/versions/0018_applications.py` -- after `0017_match_scores`; bump HEAD; owner-FK and listing-FK convention tests.
- Web: `web/src/components/listings/listing-card.tsx` (badge/action slot), `listing-detail.tsx`, `web/src/components/feed/feed-view.tsx`, `web/src/lib/auth-paths.ts`. Add `web/src/app/tracker/[id]/page.tsx`, `web/src/components/tracker/*`, `web/src/lib/tracker.ts` (+ tests). Follow the Suspense/params pattern of `/listings/[id]`.
- Tests: `api/tests/test_applications.py` (every matrix row), `test_application_transitions.py` (the full table, including the concurrency case). Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] Migration 0018; events and the error type; `tracker` models, repo, service and router.
- [x] Web: Save buttons, the `/tracker/[id]` page, helpers and tests; `npm run gen:api`.
- [x] Tests for every matrix row and the transition table.

**Acceptance Criteria:**
- Given a Student viewing a visible Listing, when they save it, mark it applied, then rejected, then reopen it, then the Application shows each Status in history newest first, the Listing is pinned, and the events were published in the same transactions.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Reopen goes only to where it was.** The history row that entered the closed Status records `from_status`, so reopening needs no extra column, and an invalid reopen is just a 409 with the one allowed target.
- **Save is idempotent.** Cards don't need to know the saved state first. A second save returns the existing Application and never double-pins.

## Implementation Notes

- **Locks instead of error mapping on save.** `save_listing` takes `FOR KEY SHARE` on the Listing (`listings.service.lock_existing`) before reading it, so it cannot be deleted before the insert. `pin` takes `FOR NO KEY UPDATE`, which does not conflict with `KEY SHARE`, so two concurrent saves never deadlock. The insert is `ON CONFLICT DO NOTHING` on the partial unique key; the loser re-reads and returns the winner's Application without pinning.
- **Resume deleted during an update.** The write runs in a savepoint; a foreign-key `IntegrityError` becomes the same 422 on `resume_id` as a Resume that is not the Student's.
- **`applied_on`** must be between 2000-01-01 and the Student's local today (both 422 otherwise). `ApplicationOut.today` carries that date for the date input's `max`.
- **`listing_available`** is true only when the Listing exists, is visible to the Student and is not expired (`listings.service.visible_items`), checked when the view is built.
- **Deleting an open Application** (not Rejected or Withdrawn) publishes `ApplicationClosed` before the row is deleted, in the same transaction, so later outreach subscribers can stop follow-ups.
- **Account deletion** releases pins: `tracker.events` subscribes to `AccountDeleting` and unpins each Listing the owner's Applications still reference, inside the deleting transaction (the cascade alone would leave them pinned).
- **History integrity.** `application_status_history` has `CHECK (from_status IS DISTINCT FROM to_status)`. History is ordered by `changed_at DESC, id DESC` (uuid7) so equal timestamps stay in order. Also added: indexes on `applications.listing_id`, `applications.resume_id` and `application_status_history.owner_id` (FK lookups for SET NULL and cascades).
- **PATCH** changes only the fields sent (`model_fields_set`); `null` clears. `notes` is capped at 4,200 in the schema (trim margin) and 4,000 after trimming in the service.
- **Web.** The Save button lives on `ListingCard` (feed, browse and search). The detail page resolves its saved state with `getApplicationForListing`, independently of the score. `/tracker/[id]` commits the applied date on blur or "Save date", asks for confirmation before Offer, counts notes in code points, and treats a 404 on load, history or transition as not-found.
- **Tests:** `test_applications.py` (matrix rows, availability, delete event, account-deletion pins, concurrent Resume deletion) and `test_application_transitions.py` (full table, reopen, events in the transaction, row-lock serialisation, and a barrier-forced concurrent save). Run against Postgres with `timezone=UTC`, as in CI.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Account deletion cascades Applications without `unpin`, so saved Listings stay pinned and are never purged or evicted | high | `applications.owner_id` CASCADE; no subscriber | patch (unpin on `AccountDeleting`) |
| 2 | `listing_available` is `listing_id is not None`, so an expired, taken-down or now-invisible Listing shows as available and links to a 404 | medium | `ApplicationOut.from_view` | patch |
| 3 | Deleting an open Application publishes nothing, so later Follow-up cleanup gets no signal | medium | `delete_application` | patch (publish `ApplicationClosed` when deleting an open Application) |
| 4 | Listing deleted between `get_listing` and insert, or Resume deleted between `owns_resume` and update, gives a 500 (FK violation) | low | no lock or IntegrityError mapping | patch |
| 5 | `applied_on` has no lower bound; the date input PATCHes on every change; client "today" uses the browser zone, the server the Preferences zone | medium | `update_details`, `application-view.tsx` | patch |
| 6 | "Offer" is terminal but needs no confirmation | low | single-click `move()` | patch |
| 7 | Notes length counted in UTF-16 units on the client vs code points on the server; request schema cap 20000 vs `MAX_NOTES` 4000 | low | `notesError`, `ApplicationUpdate` | patch |
| 8 | Web state bugs: history failure shows "HTTP 200"; id change doesn't reset state; 404 on transition not mapped; resumes not refetched on retry; detail saved state not reset; score badge waits on the saved lookup; Save button 401 has no login link | low | `application-view.tsx`, `listing-detail.tsx`, `save-listing-button.tsx` | patch |
| 9 | The concurrent-save test doesn't force the conflict branch | medium | `test_concurrent_saves_pin_once` can serialise | patch |
| 10 | No DB CHECK that a history row changes status | low | migration not yet deployed | patch |
| 11 | Cards never show the saved state (no batch lookup) | low | spec accepts idempotent saves | defer (Story 4.2) |
| 12 | No DB-role RLS test for the new tables; no component tests | low | API-level 404 tested; no web harness | defer |
| 13 | Sprint-status marks 3.4 done in this diff | false | repo convention | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 at 127.0.0.1 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
