# Epic 4 Context: Track every application

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give every Student one Tracker for all their applications. A Student saves any Listing they can see as an Application and moves it through Saved, Applied, Interview, Offer, Rejected and Withdrawn, with notes, dates, the Resume version used and a timestamped Status history. Jobs found on other sites are added as private manual Listings that are scored and tracked like pool Listings. A filterable Tracker view shows counts, upcoming deadlines and scores, and Applications survive their Listing leaving the pool. Applying itself always happens outside the platform. The epic ends with launch hardening (retention purges, isolation, anon-key lockout and performance checks, go-live checklist) so the MVP is safe to open to real Students on free tiers.

## Stories

- Story 4.1: Save a Listing and track its Status
- Story 4.2: My Tracker view
- Story 4.3: Add a job from another site
- Story 4.4: Launch hardening

## Requirements & Constraints

- **Applications:**
  - Any Listing the Student can see can be saved, creating an Application with Status Saved.
  - Status moves only along an explicit transition table: Saved → Applied or Withdrawn; Applied → Interview, Rejected or Withdrawn; Interview → Offer, Rejected or Withdrawn. Reopening from Rejected or Withdrawn returns to the prior Status.
  - An invalid transition returns 409 problem+json naming the allowed next Statuses.
  - Every Status change is timestamped in a history shown newest first.
  - Notes, an applied date and the Resume version used are editable. The Resume version must belong to the same Student.
  - Deleting an Application removes it and its history.
- **Tracker view:** filters by Status, company and date range combine, and the result count is shown. An "Upcoming deadlines" section lists Applications by deadline. Applications whose Listing was evicted or purged still appear from the snapshot, marked "no longer in the pool". A stored Match Score shows with the same "Why this score" breakdown.
- **Manual Listings:**
  - Added by URL and/or title, company, location, job type, deadline and an optional description of 4,000 characters or less, kept private to the owner.
  - The URL is stored and never fetched by the server.
  - Visible only to the owner. It never enters the pool, browse, search or other Students' feeds.
  - Scored like any other Listing and saved as an Application with Status Saved by default.
- **Isolation:** Students reach only their own Applications. Another Student's ids return 404, and admin routes return 403 to Students.
- **Launch bar:**
  - Feed pages load in under 2 s and search in under 500 ms, at about 30,000 Listings and 500 scores per Student.
  - No table is reachable with the Supabase anon key.
  - The database stays within about 0.5 GB.
  - Account deletion removes Applications, and the 30-day DPDP deadline still applies.
- **Tone:** neutral and factual, with no nudges to apply more.

## Technical Decisions

- **Module boundaries:**
  - `tracker` sits rightmost in phase 1 (`accounts ← profile ← listings ← matching ← tracker`). It may call `listings`, `matching`, `profile` and `accounts` services, and import-linter enforces this order.
  - Manual Listings belong to `listings` even though the UI lives with the Tracker.
  - Tracker code never computes scores. It reads them through `matching`.
- **Tables (owned by `tracker`):**
  - `applications` has a non-null `owner_id` (CASCADE from `accounts`) and a `listing_id` that is SET NULL when the Listing is deleted.
  - It holds a snapshot of title, company, url and deadline, taken at creation. The snapshot is read only when the Listing is gone.
  - `application_status_history` is a child table and also carries `owner_id`.
  - Every repository method takes `owner_id` from the auth context.
- **Single writer:** `tracker.service.transition()` is the only writer of Status. It writes the history row in the same transaction. Entering Rejected or Withdrawn publishes `ApplicationClosed`, and reopening publishes `ApplicationReopened`. Outreach later relies on "Applied or later" (Applied, Interview, Offer).
- **Pinning:** saving an Application calls `listings.service.pin(listing_id)` in the same transaction, and deleting one calls `unpin()`. Eviction never removes a pinned Listing.
- **Deadlines:** while the Listing exists, the deadline comes from `listings.service.effective_deadline(owner_id, listing_id)`. Story 4.2 adds it, and in phase 1 it returns the source deadline. Phase 2 adds override > extracted > source. Without the Listing, the snapshot deadline is used.
- **Manual ingestion:**
  - A `ListingDraft` with `source='manual'` and `owner_id` goes through the same `listings.service.ingest_draft()` pipeline as pool Listings (trim, skill extraction, `search_doc`).
  - On commit, `mark_stale` brings it into the owner's candidate set for scoring.
- **Unit of work:** domain events run synchronously in the caller's transaction, and only routers and tasks commit.
- **API conventions:** RFC 9457 problem+json errors with a stable `type`, cursor pagination (`items`, `next_cursor`), `/api/v1` routes, and `web` calling only the generated OpenAPI client. Untrusted text is rendered as plain text.
- **Retention:** daily, idempotent purge jobs delete finished Procrastinate jobs and outbox rows after 7 days and `ops_events` after 90 days. Expired Listings follow the 30-day grace rule.
- **Database exposure:** all tables live in schema `app` with RLS on and no `anon`/`authenticated` grants. The exposed-schema check already runs in CI. Story 4.4 adds a real anon REST probe per table.

## UX & Interaction Patterns

- There is no UX document. Screens are defined by story behaviour.
- A "Save" action appears wherever a Listing is shown: the feed, search and the detail view.
- The Application view has Status controls limited to the allowed next Statuses, fields for notes, applied date and Resume version, and a history list, newest first.
- The Tracker has combined filters with a result count, an "Upcoming deadlines" section and a "no longer in the pool" marker on snapshot rows. It reuses the Epic 3 "Why this score" breakdown.
- An "Add external job" form takes a URL or details, with an optional description. Deleting an Application needs confirmation.

## Cross-Story Dependencies

- 4.1 creates `applications`, its history, the transition table and pinning. 4.2 and 4.3 build on it.
- 4.2 adds `listings.service.effective_deadline()` and reads stored scores and breakdowns through `matching`.
- 4.3 auto-creates a Saved Application through the 4.1 service.
- 4.4 verifies isolation across every Student-owned endpoint, including Epic 4's, so it comes last.
- **From earlier epics:**
  - `listings.service.pin/unpin` and `ingest_draft`, the eviction rules and the `ListingDraft` port (Epic 2).
  - `mark_stale`, the candidate set including manual Listings, and the score breakdown (Epic 3).
  - Resume versions, `AccountDeleting`, `core.jobs` and `ops_events` (Epic 1).
- **For phase 2:** `ApplicationClosed` and `ApplicationReopened` drive Follow-up cancellation, `DeadlineChanged` drives reminders, and "Applied or later" unlocks Discovery. Keep the event contracts stable.
