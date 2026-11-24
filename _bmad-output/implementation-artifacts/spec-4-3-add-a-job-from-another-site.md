---
title: 'Story 4.3: Add a job from another site'
type: 'feature'
created: '2026-11-25'
status: 'done'
baseline_commit: 'ae77f8885c65e406a2ec7d5784ce1e7781b1a07b'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-2-my-tracker-view.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Students apply to jobs found outside the pool (company sites, other boards), but they can't track or score them (FR-18, AD-7).

**Approach:** An "Add external job" form builds a `ListingDraft` with `source='manual'` and runs it through the existing `ingest_draft` pipeline. That gives the Listing skills and `search_doc`, and keeps it private to the Student. An optional description is kept privately. The same transaction saves it as an Application in Saved and marks the Student's scores stale on the priority queue. The URL is stored but never fetched.

## Boundaries & Constraints

**Always:**
- **Migration `0020_manual_listings`:**
  - `listings.url` becomes nullable, with a CHECK that `url IS NOT NULL OR source = 'manual'`. The existing http(s) CHECK still applies when it is set.
  - Add `listings.private_description text NULL`, with CHECKs `private_description IS NULL OR source = 'manual'` and length ≤ 4,000.
  - Pool Listings stay description-free (AD-7).
- **Port:** `ListingDraft.url` becomes `HttpUrl | None`, and a model validator requires a URL unless `source == 'manual'`. All existing Sources still always send one.
- **`listings.service.create_manual_listing(session, owner_id, *, url, title, company, location, job_type, deadline, description) -> uuid`:**
  - Requires at least a title, or a URL to derive one from ("Job at <host>"). The title is 1–300 characters, the description at most 4,000 (code points), and the deadline may not be before 2000-01-01. Invalid input is a 422 with field locations.
  - `source_job_id` is a new UUID7 (one row per add).
  - Calls `ingest_draft(..., collected_by=owner_id, contributes_to_pool=False)`, so `visibility_owner_id = owner_id` and the Listing is never in the pool. Skill extraction and `search_doc` use the description, exactly as for any draft.
  - Then stores `private_description`. The description lives only in that column, is never logged, and is never returned to anyone but the owner.
  - A URL matching an active takedown block is refused with a 422 ("This job can't be added").
  - The server never makes a network request to the URL (asserted by a test that would fail on any outbound HTTP).
- **De-duplication:** if the Student already has a manual Listing with the same canonical URL (the Story 2.5 canonicaliser), it is reused instead of creating a second one. The response then returns the existing Application (200) and changes nothing.
- **Tracker endpoint `POST /api/v1/applications/manual`** (`addExternalJob`, tracker router; the router commits), in one transaction:
  - create or reuse the manual Listing;
  - `tracker.service.save_listing` (Saved, snapshot, pin);
  - `matching.service.mark_stale([owner], reason="manual_listing", priority=True)`;
  - then start the after-response priority drain.
  - Returns 201 with the `ApplicationOut` for a new one, 200 with the existing Application for a duplicate URL.
- **Delete cleanup:** when an Application whose Listing is the Student's own manual Listing is deleted (`delete_application`), the manual Listing is deleted too, in the same transaction, after unpinning. Its scores cascade, and nothing else can reference it.
- **Visibility is already enforced and must stay so:** every browse, search, feed, batch-score and by-listings path filters `visibility_owner_id IS NULL OR = actor`. A test asserts that another Student never sees the manual Listing anywhere: browse, search, detail, feed, `/matches`, `/listings/{id}/match`, or saving it (404).
- **Owner-facing output:**
  - `ApplicationOut` gains `is_manual: bool` and `description: str | null` (only for the owner's manual Listing; null otherwise).
  - `ListingOut.url` becomes nullable. The web hides "Apply" when there is no URL.
  - Manual Listings show "Added by you" instead of "via <Source>" (`sourceAttribution` already returns null for manual; add the label).
- **Web:**
  - A protected `/tracker/new` page, "Add external job", linked from `/tracker`. Fields:
    - URL;
    - title, required unless a URL is given;
    - company;
    - location;
    - job type (Internship / Full-time / not specified);
    - deadline;
    - description, with a live code-point counter out of 4,000.
  - The form shows field errors from the 422, then redirects to `/tracker/[id]` on 201 or 200.
  - A short note under the URL field: "We don't visit this link; it's saved so you can open it later."
  - `/tracker/[id]` shows the description (plain text, whitespace preserved) for manual Applications, plus "Added by you".
  - Pure helpers (form validation mirroring the API, the derived-title preview) go in `web/src/lib/manual-listing.ts` with tests. Regenerate the client.

**Never:**
- Fetching the URL, scraping it, or previewing link metadata.
- Putting a manual Listing in the pool or showing it to anyone else.
- Storing a description on a pool Listing.
- Editing a manual Listing after creation (later).
- A second scorer or a separate ingest pipeline.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Full form | URL, title, company, location, internship, deadline, description mentioning Python and Docker | 201; manual Listing private to the owner; skills include python and docker; `private_description` stored; Application Saved and pinned; priority recompute queued | N/A |
| URL only | `https://jobs.example.com/123` | title "Job at jobs.example.com"; 201 | N/A |
| Title only | no URL | 201; `url` NULL; no Apply link | N/A |
| Neither | no title, no URL | 422 on title | N/A |
| Bad URL | `ftp://x` or `javascript:alert(1)` | 422 on url | N/A |
| Long description | 4,001 code points | 422 | N/A |
| Old deadline | 1999-12-31 | 422 | N/A |
| Duplicate URL | same canonical URL again by the same Student | 200, the existing Application; no second Listing | N/A |
| Same URL, other Student | another Student adds the same URL | their own separate private Listing | N/A |
| Blocked URL | URL under a takedown block | 422; nothing written | N/A |
| No fetch | any URL | no outbound HTTP during the request | N/A |
| Privacy | another Student | never in browse, search, feed, detail, `/matches` or `/match`; saving it is a 404 | N/A |
| Scored | after the drain | a stored `match_scores` row for the manual Listing (manual Listings are always in the candidate set) | N/A |
| Delete | delete the Application | manual Listing deleted too; scores gone; a pool Listing is never deleted this way | N/A |
| Account deleted | owner removed | manual Listings and Applications cascade | N/A |
| Signed out | any route | 401 | N/A |

</frozen-after-approval>

## Code Map

- `api/app/ports/source_adapter.py` -- `ListingDraft`, `HttpUrl`, `MAX_DESCRIPTION_CHARS`. Make `url` optional for manual only.
- `api/app/modules/listings/service.py` / `repo.py` -- `ingest_draft`, `_upsert`/`_UPSERT` (`url` bind may be NULL), `canonical_url`, `_block_target`/blocked checks (Story 2.5), `MANUAL_SOURCE`, the visibility predicate. Add `create_manual_listing`, a "manual Listing by canonical URL for owner" lookup, `delete_manual_listing`.
- `api/app/modules/tracker/service.py` / `router.py` / `schemas.py` -- `save_listing`, `delete_application`, `ApplicationOut`; add `add_external_job` and the route; `matching.service.mark_stale`; `core.jobs.drain_priority_after_response` (see the profile and matching routers for the pattern).
- `api/app/modules/listings/schemas.py` -- `ListingOut.url` nullable.
- `api/migrations/versions/0020_manual_listings.py` -- after `0019_applications_created_idx`; bump HEAD.
- Web: `web/src/app/tracker/page.tsx` and `web/src/components/tracker/*` (4.1 and 4.2), `web/src/lib/listings.ts` (`sourceAttribution`, `applyLabel`), `listing-card.tsx` and `listing-detail.tsx` (hide Apply when `url` is null). Add `web/src/app/tracker/new/page.tsx`, `web/src/components/tracker/add-external-job-form.tsx`, `web/src/lib/manual-listing.ts` (+ tests).
- Tests: `api/tests/test_manual_listings.py` (every matrix row; for "No fetch", monkeypatch `httpx.AsyncClient.send` / socket connect to fail the test). Reuse `tracker_fixtures.py` and `match_fixtures.py`. Postgres at 127.0.0.1 with `timezone=UTC`. Low-entropy fake ids only.

## Tasks & Acceptance

**Execution:**
- [x] Migration 0020; port change; `listings.create_manual_listing`, lookups and delete; tracker `add_external_job`, route and delete cleanup; schema changes.
- [x] Web `/tracker/new` form, manual display on `/tracker/[id]`, nullable-URL handling; helpers and tests; `npm run gen:api`.
- [x] Tests for every matrix row.

**Acceptance Criteria:**
- Given a Student who adds an external job with a description through `/tracker/new`, when the request completes and the drain runs, then they land on its Application page (Saved, "Added by you", their description), it has a stored Match Score, and no other Student can see it anywhere.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **One pipeline.** Manual Listings use `ingest_draft` like scraped ones, so skills, `search_doc`, takedown checks and the `ListingsIngested` event behave the same. Only visibility and the private description differ.
- **Deleting the Application deletes its manual Listing.** A manual Listing exists only for that Application. Keeping it would leave invisible, pinned rows that nothing can reach.

## Implementation Notes

- **`applications.url` is nullable too** (migration 0020): a job added by title only has no link to snapshot, so `ApplicationOut.url` is `string | null` like `ListingOut.url`. The downgrade writes `''` into NULL snapshot URLs and deletes URL-less Listings before restoring the NOT NULL columns.
- **Validation** lives in `listings.service._manual_draft`: text is trimmed; limits are checked in code points (title 1–300, company and location 200, link 2,048, description 4,000); a missing title with a link becomes `Job at <host>`, where the host is the raw authority as typed (`raw_host`: after `scheme://` up to `/`, `?` or `#`, no userinfo or `:port`, lower-cased, no IDNA; IPv6 keeps its brackets), mirrored by the web's `linkHost`; the link is checked by the port's `HttpUrl` rule (any failure is reported on `url`). Each 422 carries one `{loc: ["body", field], msg}`; the request schema (`ExternalJobIn`) caps each field at its limit + 200 so whitespace can be trimmed first. Pydantic errors are read with `include_input=False`, so the description never reaches an error or a log.
- **Takedowns:** a URL block (canonical form) or a company block refuses the add (422 "This job can't be added." on `url`, or on `company` when there is no link), checked before any reuse as well as in the ingest pipeline. Nothing is written. Per the amended rule, a later URL or company takedown sweep skips `source='manual'` Listings (`repo.listing_ids_and_urls` / `listing_ids_and_companies`); a `listing` takedown is unchanged.
- **De-duplication** keys on `canonical_url(url, keep_query=True)` (scheme and host lower-cased, default port and userinfo dropped, trailing slash and fragment dropped, query kept) over the Student's own manual Listings, expired or not (`repo.manual_urls`), under a per-Student advisory lock so two adds of one link at once cannot both create. `create_manual_listing` returns `(listing_id, reused)`. On a reuse the Tracker looks the Application up by Listing id for the owner (no expiry check) and returns it unchanged with 200, marking nothing; when that Listing has no Application, a new manual Listing is created (`reuse_existing=False`). A pool Listing with the same URL is never reused.
- **Owner-facing facts in one query:** `listings.service.tracker_facts` (repo `visible_facts`) returns availability (not expired), the effective deadline, "is the owner's manual Listing" and, when asked, the private description, replacing the tracker's `effective_deadlines` call (still exported). The owner's own manual Listings are returned even when expired (`available` False), so "Added by you" and the description survive expiry while availability, deadlines and scores keep the not-expired rule. (The expire job skips pinned Listings, so a manual Listing with its Application normally never expires.) Single-Application responses include the description; Tracker list rows carry `is_manual` but `description: null`, so the 4.2 query budget is unchanged.
- **Scoring:** `ingest_draft` publishes `ListingsIngested` as for any draft (the owner is marked stale on the default queue), and `add_external_job` also calls `mark_stale(reason="manual_listing", priority=True)`; the router drains the priority queue after the response only for a new add.
- **Web:** `/tracker/new` (`components/tracker/add-external-job-form.tsx`) validates with `lib/manual-listing.ts` before sending and maps 422 `errors[].loc[1]` to fields (anything else goes to a form-level message). The link check mirrors the API without `new URL()` (`^https?://`, no unsafe characters, a non-empty raw host), leaving the rest to the API. Editing a field clears its error; typing a link clears only the "title required" error. A 401 keeps the form on screen, stores the draft in `sessionStorage` and links to `/login?next=/tracker/new`; the draft is restored (and removed) on the next load. A 200 redirects to `/tracker/[id]?existing=1`, where "You already added this link." shows once (the flag is dropped from the URL). The counter is visible on every keystroke but announced (an `sr-only` live region) only within 200 of the limit or over it. Tracker rows show "Added by you" for `is_manual`. The title hint previews the derived title. `ListingAttribution` now always shows a Source line: "via <Source>", or "Added by you" for a manual Listing (`lib/listings.sourceLine`). `/tracker/[id]` shows "Added by you" and the description (`whitespace-pre-wrap`, plain text).

## Spec Change Log

- **2026-11-25, review pass 1 (findings 2 and 4):** two rules amended under the user's standing approval.
  - **De-duplication key:** the takedown canonicaliser drops the query string, so two different jobs on one board (`?gh_jid=1` / `?gh_jid=2`) collapsed into one. Manual de-duplication now uses the canonical URL with its query string kept (scheme, host and default port normalised; fragment dropped).
  - **Takedown scope:** a later URL or company takedown no longer deletes a Student's private manual Listings, which are never distributed. A block still refuses new adds. KEEP: one ingest pipeline, private visibility, no URL fetch, save as Saved with a priority recompute, delete cleanup.

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | After a manual Listing expires (deadline passed), `tracker_facts` drops it, so `is_manual` and the description vanish from `/tracker/[id]`; re-adding the link returns an expired Listing and the save fails | high | `visible_facts` filters `expired_at IS NULL`; `manual_urls` doesn't | patch |
| 2 | Manual de-duplication uses the takedown canonical form, which drops the query string, merging distinct jobs | high | `canonical_url` strips query | patch (spec amended) |
| 3 | The duplicate path returns an existing Listing without re-checking takedown blocks | low | `create_manual_listing` | patch |
| 4 | A later takedown sweep deletes Students' private manual Listings | medium | `_blocked_listing_ids` scans all rows | patch (spec amended: exclude manual Listings) |
| 5 | Web: a 401 discards the typed form; duplicate redirect gives no notice; IDN, lenient-scheme and out-of-range-port links disagree with the server; the counter is announced every keystroke; the title error is cleared by typing a link | medium | `add-external-job-form.tsx`, `manual-listing.ts` | patch |
| 6 | The Tracker list never shows "Added by you" | low | `is_manual` unused in `tracker-view.tsx` | patch |
| 7 | Tests: a delete assertion passes for any status; no URL takedown with a URL-less manual Listing present; the description-logging test never reaches the service path | medium | test files | patch |
| 8 | No per-Student cap or rate limit on manual adds | medium | each add ingests and recomputes | defer (Story 4.4) |
| 9 | De-duplication rescans all manual URLs in Python; no DB-level uniqueness; no concurrent-add test; no downgrade test with NULL-URL rows | low | advisory lock serialises adds | defer |
| 10 | Each add queues a default and a priority recompute | false | the queueing lock folds them; the priority job refreshes after the response | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with `timezone=UTC`, at 127.0.0.1, roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
