# Epic 2 Context: Fresh LinkedIn jobs in a searchable shared pool

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Fill CareerLaunch with real, fresh job and internship Listings. A Student adds a scoped Apify token, and scheduled collection runs, billed to the Student's own Apify account and scoped to their Preferences, pull LinkedIn Listings into one shared, deduplicated pool. Every Student can browse and search that pool, including Students without a key. The pool stays minimal, attributed, within free-tier storage and expires on schedule. The epic also sets up the Source Adapter seam and the single Apify spend path that later outreach work will reuse. Epic 3 ranks this pool and Epic 4 pins Listings from it.

## Stories

- Story 2.1: Shared Listing pool, Source Adapter and browsing
- Story 2.2: Add my Apify Provider Key
- Story 2.3: Collect LinkedIn Listings on a schedule
- Story 2.4: See my usage, set spend caps, and pause on failure
- Story 2.5: Expire, evict and take down Listings
- Story 2.6: Search Listings by keyword

## Requirements & Constraints

- **Collection:**
  - Runs only for Students with a valid Provider Key and `provider_runs` consent, scoped to their role titles, locations and job types.
  - Each Listing stores title, company, location, job type, posted date, deadline (if present) and source URL. A duplicate is stored once.
  - Saving a valid key triggers a first run, so fresh Listings arrive within the hour.
  - Before launch, the owner seeds the pool with a run under the owner's own key, so the first feed is never empty.
  - A Student without a key still sees the shared pool.
- **Pool contribution:** Listings enter the shared pool only with the collecting Student's `pool_contribution` consent. Otherwise they stay visible only to the collector. If a consenting Student later collects the same Listing, it becomes public.
- **Minimal content:** pool Listings never persist the description. It is used at ingestion to extract skills and search lexemes, then discarded.
- **Attribution:** every Listing shows its source ("via LinkedIn") and a not-affiliated notice. "Apply" opens the source URL. Scraped text is rendered as plain text.
- **Provider Key:**
  - Settings show short setup steps: a free Apify account, then a scoped token (Run permission on approved actors only, restricted access, with an expiry), noting USD 5 of free monthly credit.
  - The key is validated on save, and an invalid key is rejected clearly.
  - It is stored encrypted and never shown again, logged or sent to the client.
  - It is replaceable or deletable, and deletion stops all runs.
  - Saving a key without `provider_runs` consent redirects to the consent screen.
- **Usage and caps:**
  - The Student sees estimated and actual cost, item count and outcome per run.
  - Per-run, monthly and platform-wide (env) caps refuse runs, with a visible reason.
  - If Apify reports an invalid key, a blocked account or exhausted credit, collection pauses for that Student with the reason shown. Exhausted credit pauses until the next billing cycle. The shared pool keeps serving them.
- **Expiry and capacity:**
  - Listings past their deadline, or no longer seen at the Source for a configured number of runs, are marked expired and hidden by default. They are deleted after a 30-day grace period.
  - The pool is capped at about 30,000 active Listings. Eviction removes the oldest, least relevant unpinned Listings first and never removes a pinned one.
  - A daily check raises an alert at 80% of the DB quota (about 0.5 GB).
- **Takedown:** the owner can block a Listing, URL or company. Matching rows are removed and never re-ingested, and the action is audited.
- **Search:**
  - Keyword search covers title, company, extracted skills and description lexemes, ranked by relevance and tolerant of typos ("bakend intrn" still works).
  - It returns only Listings visible to the Student that are not expired.
  - It takes under 500 ms at about 30,000 Listings, checked by a performance test.
  - An empty result shows a neutral prompt to broaden the terms.
- **Non-functional:** ingestion jobs are idempotent and retry safely. A failed or disabled Source stops only its own collection. Every run is logged and visible to the owner. Free tiers only, so nothing may wait synchronously on Apify.

## Technical Decisions

- **Ports:**
  - `SourceAdapter` yields a Pydantic `ListingDraft` (`source, source_job_id, url, title, company_raw, location_raw, job_type, description, posted_at: datetime|None, deadline: date|None`).
  - `ProviderRunner` wraps the Apify API.
  - Each port has an in-memory fake in `api/tests/fakes/`. Raw provider output is mapped to `ListingDraft` and never stored. Apify SDK and HTTP code lives only in `adapters/apify/`.
- **Single ingest pipeline:**
  - `listings.service.ingest_draft(draft, collected_by, contributes_to_pool)` upserts on the unique `(source, source_job_id)` and extracts skills via `core.skills.normalize()` with `vocab_version`.
  - It builds the `search_doc` tsvector, drops the description, and publishes `ListingsIngested` in the same transaction.
  - Manual Listings (Epic 4) reuse this pipeline with `source='manual'` + `owner_id`.
- **Listing ownership:**
  - `listings` and `sources` are global tables with no `owner_id`. Their service functions take an actor (Student, Admin or System).
  - Visibility is controlled by `visibility_owner_id` (NULL means pool). `collected_by` is SET NULL on account deletion.
  - `sources.enabled` gates collection per Source.
- **Hooks for later epics:**
  - `listings` gains `pin_count` and `relevance`, and exposes `pin()`, `unpin()`, `set_relevance()`, `block()` and `effective_deadline()`.
  - Foreign keys into `listings` declare CASCADE for score tables and SET NULL for `applications.listing_id`.
- **Single Apify spend path:** `listings.service.start_provider_run(owner_id, purpose, params)` owns `provider_runs` and is the only caller of `ProviderRunner`. In order, it:
  1. Re-checks `require_consent`, the per-Student `provider_status` (owned by `accounts`, default `active`, `paused` with a reason, `exhausted_until`), Source and provider enablement, and all caps.
  2. Commits the row as `starting`.
  3. Starts an allowlisted actor asynchronously, with max charge and max items set and no cookie or login inputs, and stores the run id.
  4. Never starts again for a row that already has a run id.
- **Actors and request headers:** the allowlist in config is `curious_coder/linkedin-jobs-scraper` (primary, about $1 per 1,000 results) and `harvestapi/linkedin-job-search` (fallback, with its optional cookie field never set). Confirm both still exist and check their input schemas before building. Every request sends `x-apify-integration-platform`, and keys are validated with `GET /v2/users/me`.
- **Polling:** `listings.poll_provider_run:provider_run:<id>` reschedules itself with backoff. On success it maps output to drafts, ingests them, and records the actual cost and outcome. Failures go to `ops_events` and are retried. No network calls happen inside a DB transaction.
- **Scheduling:**
  - Saving a key creates a `schedules` row (kind `ingest`, `owner_id`) and enqueues a first run.
  - `scheduler_tick` enqueues `listings.collect:student:<id>:<local_date>`.
  - Pausing a key, provider or Source pauses its schedule rows, which takes effect on the next tick.
  - A paused status resumes on the next tick once the key is fixed or the billing cycle resets.
  - Expiry, purge, eviction and quota checks are daily jobs. Eviction uses `FOR UPDATE SKIP LOCKED` on candidate rows.
- **Secrets:**
  - `core.crypto` uses AES-GCM with additional authenticated data `table:row_id:owner_id`, a key-version prefix and a master key from Render env.
  - Keys are decrypted only inside the adapter.
  - APIs return `has_key` + last-4 only, and log formatters redact the key.
- **Search:** Postgres full-text search on `search_doc` plus `pg_trgm` trigram similarity, with GIN indexes. `listings.repo` may read `match_scores` for ranking (Epic 3).
- **Admin actions:** phase 1 has no admin UI. Takedown and seeding are CLI or DB operations, audited via `core.ops.record_audit()`.

## UX & Interaction Patterns

- There is no UX document. Screens are defined by story behaviour.
- **Browse:** Listings are newest first with cursor pagination. Cards and detail views carry the source badge and the not-affiliated notice, and their Apply link goes to the source.
- **Provider Key settings:** the numbered setup steps, a paste field, clear validation errors, a masked last-4 hint after saving, and replace and delete actions. The same area shows a usage table (estimated and actual cost, items, outcome), cap inputs, and a visible paused or refused reason.
- **Students without a key:** they still get a full pool experience, plus a reminder that adding a key unlocks fresh collection.
- **Tone:** neutral and factual. The search empty state suggests broadening terms.

## Cross-Story Dependencies

- 2.1 comes first, because it provides the ports, the `listings` and `sources` tables, `ingest_draft` and browsing. 2.3, 2.5 and 2.6 build on it.
- 2.2 (key storage and `ProviderRunner` validation) comes before 2.3. 2.3 creates `provider_runs`, `provider_status` and the ingest schedule. 2.4 extends `start_provider_run` with caps and pause handling.
- 2.6 relies on `search_doc` from 2.1 and on expiry and visibility rules from 2.1 and 2.5.
- **From Epic 1:**
  - `require_consent` (`provider_runs`, `pool_contribution`).
  - `ConsentRevoked`, which must pause the Student's schedules and cancel pending runs.
  - `PreferencesChanged`, which rescopes the next run.
  - `schedules`, `scheduler_tick`, `core.jobs.enqueue`, `core.skills`, `core.ops` and `AccountDeleting` (removes keys, sets `collected_by` to NULL).
- **For later epics:**
  - Epic 3 subscribes to `ListingsIngested` and calls `set_relevance`.
  - Epic 4 calls `pin()` and `unpin()`, `effective_deadline()`, and `ingest_draft` for manual Listings.
