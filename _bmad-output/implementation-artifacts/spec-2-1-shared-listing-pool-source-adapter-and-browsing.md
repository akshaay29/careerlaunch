---
title: 'Story 2.1: Shared Listing pool, Source Adapter and browsing'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '4ac1e08fe936abc4cb052bcfff607d30e0ee6b23'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CareerLaunch has no Listings. Collection (2.3), search (2.6), ranking (Epic 3) and tracking (Epic 4) all need three things that don't exist yet:
- a Source seam;
- one ingest pipeline that stores only minimal pool content;
- a visibility rule for the shared pool.

Students also need to browse Listings even without an Apify key (FR-7, FR-8, AD-3, AD-7).

**Approach:**
- Add a `SourceAdapter` port that yields `ListingDraft`s, with a fake.
- Add global `sources` and `listings` tables.
- Add `listings.service.ingest_draft`, which:
  - upserts on `(source, source_job_id)`;
  - normalises skills;
  - builds `search_doc` from the description;
  - discards the description;
  - sets visibility from `pool_contribution` consent;
  - publishes `ListingsIngested`.
- Add a browse API and a `/listings` UI with attribution.

## Boundaries & Constraints

**Always:**
- **`app/ports/source_adapter.py`** (imports only `core`) defines:
  - `ListingDraft` (Pydantic, frozen): `source`, `source_job_id`, `url`, `title`, `company_raw`, `location_raw`, `job_type: Literal['internship','full_time'] | None`, `description`, `posted_at: datetime | None`, `deadline: date | None`. Validation:
    - every text field is stripped and length-capped: title ≤ 300, company and location ≤ 200, `source_job_id` ≤ 200, description ≤ 100,000 characters;
    - `url` is `http(s)` only and ≤ 2048 characters.
  - `SourceQuery` (`role_titles`, `locations`, `job_types`, `limit`).
  - The protocol `SourceAdapter` with `source: str` and `collect(query) -> AsyncIterator[ListingDraft]`.
- **Fake:** `api/tests/fakes/source_adapter.py` yields configured drafts.
- **Table `app.sources`** (global):
  - Columns: `id text PK`, `label`, `enabled bool NOT NULL DEFAULT true`, `created_at`.
  - Migration seeds `('linkedin', 'LinkedIn', true)` and `('manual', 'Manual', true)`.
  - RLS on.
- **Table `app.listings`** (global, no `owner_id`):
  - Identity and source: `id` (uuid v7), `source` (→ sources), `source_job_id`, `url`.
  - Listing fields: `title`, `company`, `location`, `job_type`, `posted_at`, `deadline date`.
  - Skills and search: `skills text[]`, `vocab_version`, `search_doc tsvector`.
  - Seen dates: `first_seen_at`, `last_seen_at`.
  - Collection and visibility:
    - `collected_by` (→ accounts ON DELETE SET NULL);
    - `visibility_owner_id` (→ accounts ON DELETE CASCADE; NULL means pool).
  - Later-epic hooks: `pin_count int NOT NULL DEFAULT 0`, `relevance real NOT NULL DEFAULT 0`, `expired_at timestamptz NULL`.
  - Constraints and indexes:
    - `UNIQUE (source, source_job_id)` and CHECKs matching the draft limits;
    - a GIN index on `search_doc`;
    - an index for browse `(first_seen_at DESC, id DESC)`;
    - an index on `visibility_owner_id`.
  - RLS on.
  - No column holds the description.
- **`listings.service.ingest_draft(session, draft, *, collected_by, contributes_to_pool)`**:
  - `contributes_to_pool` is the caller's result of `accounts.service` consent for `pool_contribution`.
  - It never commits. In one statement it upserts on `(source, source_job_id)`:
    - `skills` = `core.skills.normalize(title + description)` with `vocab_version`;
    - `search_doc` = `setweight(to_tsvector('english', title), 'A') || setweight(company, 'B') || setweight(skills labels, 'C') || setweight(description, 'D')`, built in SQL from parameters;
    - the description is passed only as a bind parameter and never stored or logged.
  - On insert:
    - `visibility_owner_id` = NULL if `contributes_to_pool`, else `collected_by`;
    - `first_seen_at` = `last_seen_at` = now.
  - On conflict:
    - refresh the mutable fields (title, company, location, job type, dates, url, skills, `search_doc`, `last_seen_at`);
    - visibility only widens: a contributing collector sets it to NULL and becomes `collected_by`. A private Listing never changes owner, and a pool Listing never becomes private.
  - Returns `(listing_id, created)`.
- **`listings.service.collect_and_ingest(session, adapter, query, *, collected_by)`** is used by 2.3 and the seed script:
  - If `sources.enabled` is false for `adapter.source`, it returns 0 without calling the adapter.
  - Otherwise it reads `pool_contribution` consent once through `accounts.service`, ingests each draft, and publishes one `ListingsIngested(listing_ids, collected_by)` (added to `core.events`) per call, in the same transaction.
- **Browse API** (under `/api/v1`, token required):
  - `GET /listings?cursor&limit` (default 20, max 50) returns Listings visible to the actor (`visibility_owner_id IS NULL OR = actor`) and not expired, newest first by `(first_seen_at, id)`.
  - `GET /listings/{id}` returns the same shape for a visible Listing; anything else gives 404.
  - Item fields: `id`, `title`, `company`, `location`, `job_type`, `posted_at`, `deadline`, `url`, `source`, `source_label`, `skills` (`[{id, label}]`), `first_seen_at`.
- **Local seed:** `api/scripts/seed_demo_listings.py` ingests about 30 realistic fake drafts into the pool under a System collector (`collected_by` NULL, contributes to pool), so the browse UI can be checked locally. It is idempotent.
- **Web:**
  - `/listings` (protected) shows cards with title, company, location, job type, posted or deadline dates and skill chips, plus "Load more".
  - `/listings/[id]` is the detail page.
  - Every card and the detail page show "via {source_label}" and the notice "CareerLaunch is not affiliated with LinkedIn or any job board."
  - "Apply" is an external link to `url` (`target="_blank" rel="noopener noreferrer nofollow"`), rendered only for `http(s)`.
  - All scraped text is rendered as plain text.
  - The onboarding "You're set up" screen and the `/settings` hub link to `/listings`.
  - The empty state reads "No Listings yet — new ones arrive as collection runs."

**Never:**
- Persisting or logging the description.
- Collection runs, Apify, Provider Keys or schedules (2.2–2.4).
- Search (2.6). Expiry jobs (2.5; the `expired_at` column exists and browse already hides expired rows). Scoring (Epic 3). Manual Listings UI (Epic 4).
- An `owner_id` column on `listings`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Ingest new | draft from a consenting collector | 1 row; pool visibility; skills with `vocab_version`; `search_doc` non-empty; no description stored anywhere | N/A |
| Duplicate | same `(source, source_job_id)` again | still 1 row; `last_seen_at` advanced; fields refreshed | N/A |
| Private | collector without `pool_contribution` | `visibility_owner_id` = collector; other Students can't see it (browse and 404 on detail) | N/A |
| Widen | the private Listing is later collected by a consenting Student | `visibility_owner_id` NULL; everyone sees it | N/A |
| Never narrows | pool Listing re-collected by a non-consenting Student | stays in the pool | N/A |
| End to end | fake Source with 20 drafts through `collect_and_ingest` | `ListingsIngested` once; browse returns them newest first; cursor pages through all 20 with no tracker or matching changes | N/A |
| Disabled Source | `sources.enabled=false` | returns 0; adapter not called; existing Listings still browse | N/A |
| Invalid draft | `javascript:` URL; over-length title | `ListingDraft` validation error before any write | ValidationError |
| Description privacy | after ingest | no column or `ops_events` row contains description text; `search_doc` still matches a description-only word | N/A |
| Expired hidden | `expired_at` set | not in browse; detail 404 | N/A |
| Account deleted | collector deleted | pool rows keep `collected_by` NULL; their private rows cascade away | N/A |
| Auth | no token | 401 | problem+json |

</frozen-after-approval>

## Code Map

Builds on Epic 1. Reuse it; don't restructure.
- `api/app/ports/file_store.py`, `api/app/ports/auth_admin.py` -- port module pattern (Protocol, plus install/get if needed). `SourceAdapter` doesn't need process-wide install yet; callers pass the adapter in.
- `api/app/core/events.py` -- add `ListingsIngested(listing_ids: tuple[uuid.UUID, ...], collected_by: uuid.UUID | None)`.
- `api/app/core/skills.py` -- `normalize`, `label`, `VOCAB_VERSION`.
- `api/app/modules/accounts/service.py` -- `require_consent` and the consent repo. Add a non-raising `has_consent(session, owner_id, purpose) -> bool`, or reuse what exists. `listings` may import `accounts` (module order).
- `api/app/modules/listings/*` -- stubs today; fill in models, repo, service, schemas and router (`tags=["listings"]`, explicit `operation_id`s). Routers commit; services never do.
- `api/app/modules/profile/service.py` `_split_page` / resume list -- the cursor pagination pattern.
- `api/migrations/versions/0010_deleted_accounts.py` -- latest; the new one is `0011_listings.py`. Bump `HEAD`. `test_owner_fk_convention.py` must keep passing (`listings` has no `owner_id`; its FKs to accounts are CASCADE or SET NULL).
- `web/src/lib/auth-paths.ts` -- add `/listings` to the protected prefixes. `web/src/components/consent/onboarding-consent-step.tsx` has the done screen. `web/src/app/settings/page.tsx` is the hub.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/ports/source_adapter.py`, `api/tests/fakes/source_adapter.py` -- `ListingDraft`, `SourceQuery`, the protocol and the fake.
- [x] `api/app/core/events.py` -- `ListingsIngested`.
- [x] `api/migrations/versions/0011_listings.py` -- `sources` with seeds, and `listings` with constraints, indexes and RLS.
- [x] `api/app/modules/listings/*`, `api/app/modules/accounts/service.py` (`has_consent` if needed) -- `ingest_draft`, `collect_and_ingest`, browse/detail repo and routes.
- [x] `api/scripts/seed_demo_listings.py`, `README.md` -- the local seed and how to run it.
- [x] `api/tests/test_listings_ingest.py`, `test_listings_browse.py`, `test_source_adapter.py` -- every matrix row.
- [x] `web/**` -- regenerate the client; add the listings list and detail pages, the attribution component, links from onboarding and the hub, the protected path, and helper tests (date and job-type formatting, safe-URL check).

**Acceptance Criteria:**
- Given the seed script on the local stack, when a signed-in Student opens `/listings`, then the demo Listings show with "via LinkedIn", the not-affiliated notice and working Apply links.
- Given the full suite, when it runs, then every Epic 1 test still passes.

## Design Notes

- **One row per `(source, source_job_id)`; visibility only widens.** If two Students without pool consent collect the same Listing, it stays private to the first. The second Student sees it once anyone with consent collects it. This is a deliberate limit of the single `visibility_owner_id` model (AD-7). 2.3 can revisit it if real data shows it matters.
- **`search_doc` is built in SQL from bind parameters.** The description reaches Postgres only as a parameter to `to_tsvector`, so the lexemes are stored but the text never is.

## Implementation Notes

- `collected_by` changes on conflict only when the row widens (private -> pool). A contributing collector re-collecting a pool row leaves `collected_by` as is, so a System seed re-run never clears a Student's attribution.
- `first_seen_at` / `last_seen_at` use `clock_timestamp()`, so a re-collection in the same transaction still advances `last_seen_at`; uuid v7 ids keep the browse tiebreak in insertion order.
- The browse cursor is opaque (base64url of `first_seen_at|id`); a malformed one is a 422 on `cursor`.
- `collect_and_ingest` publishes `ListingsIngested` (and records `listings.ingested` with counts only) when at least one draft was ingested; an empty run publishes nothing. An unknown Source is treated like a disabled one (returns 0, adapter not called). A draft whose `source` differs from `adapter.source` raises `ValueError`.
- `ingest_draft` rejects `collected_by=None` with `contributes_to_pool=False` (the System always contributes), and re-raises DB errors as `ListingIngestError` with the original (parameter-carrying) error suppressed, so the description cannot reach a log.
- Review fixes: on conflict only an authorised writer (contributing, or the collector the row is private to) refreshes content; others only advance `last_seen_at`, and rows not visible to the collector are left out of the count and the event. An authorised refresh keeps known values when the new scrape is thinner (per-weight `ts_filter` keeps the old `search_doc` parts for an empty company, no skills or no description) and clears `expired_at` when the deadline is unknown or not past. `ingest_draft(..., publish=True)` publishes its own event; `collect_and_ingest` uses the same upsert without per-draft events, runs each draft in a savepoint (refused drafts counted as `rejected`), stops at `query.limit`, and records `listings.collect_skipped` for a disabled or unknown Source. The seed uses its own `demo` Source ("Demo data") and runs only in `local`/`ci`. `listings_browse_idx` is partial on `expired_at IS NULL`.
- `ListingDraft.description` is hidden from `repr`; unknown draft fields are rejected (`extra="forbid"`).
- `/listings/[id]` reads `params` inside `<Suspense>` (the app uses Cache Components; a top-level `await params` fails prerendering).

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A collector without `pool_contribution` consent still overwrites a pool row's content (title, company, url, skills, `search_doc`, …), and the same goes for another Student's private row. That shares their scrape without consent. Their count and `ListingsIngested` also include rows they can't see. | high | `_UPSERT` ON CONFLICT refreshes content unconditionally; only visibility is guarded | patch |
| 2 | `ingest_draft` doesn't publish `ListingsIngested` (the frozen intent says it does); only `collect_and_ingest` does, so direct callers (manual Listings, Epic 4) would emit nothing | medium | `service.ingest_draft` has no publish | patch |
| 3 | One failing draft aborts the whole batch transaction, losing earlier drafts | medium | No savepoint per draft in `collect_and_ingest` | patch |
| 4 | A thinner re-scrape erases known values (dates, job type, company, location) with NULL or empty | medium | `EXCLUDED.*` copied straight across | patch |
| 5 | A re-sighted Listing never clears `expired_at`, so it stays hidden forever | medium | The refresh doesn't touch `expired_at` | patch |
| 6 | `query.limit` isn't enforced on adapter output | medium | `collect_and_ingest` consumes everything the adapter yields | patch |
| 7 | Draft hardening: NUL characters crash Postgres; naive `posted_at` shifts by the session time zone; the server accepts URLs with backslash or Unicode whitespace that the web then refuses to link | low | `ListingDraft` validators; direct fixes | patch |
| 8 | Seed script: blocks only `prod` (should be local only); fake Listings carry the "LinkedIn" label; a 0-count message always says "disabled" | low | `seed_demo_listings.main`; direct fixes (a local `demo` Source, an environment allowlist, distinct messages) | patch |
| 9 | Skipped or empty runs leave no ops trace | low | Return 0 silently; direct `record_event` | patch |
| 10 | "Apply on Manual" / "via Manual" wording for the manual Source; a skill label that fails after a vocabulary change; the first-load error has no retry; `listingMeta` sits outside the tested lib | low | Direct UI and fallback fixes | patch |
| 11 | Missing tests: keyset ties on `first_seen_at`, exact-multiple page sizes, pool row keeps its first collector across consenting collectors and System, seed prod refusal | medium | Gaps filed pre-verified | patch |
| 12 | Browse filter `expired_at IS NULL` lacks a partial index | low | `listings_browse_idx` isn't partial; direct migration tweak (0011 not deployed) | patch |
| 13 | No component tests for `ListingBrowser` / `ListingDetail` | medium | No `web` harness, as in earlier stories | defer |
| 14 | `RETURNING (xmax = 0)` relies on a Postgres internal | low | A well-known idiom on heap tables; Supabase uses heap | reject |
| 15 | A stale cursor 422 on "Load more" is retried forever | low | Cursors are opaque and stable; a 422 means a corrupted client state | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack: run the seed script, sign in, and browse `/listings` and a detail page.
