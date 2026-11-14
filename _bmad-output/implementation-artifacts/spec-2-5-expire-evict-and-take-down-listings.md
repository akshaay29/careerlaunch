---
title: 'Story 2.5: Expire, evict and take down Listings'
type: 'feature'
created: '2026-11-15'
status: 'done'
baseline_commit: '37d5c978bcf6c5ec37d6b37a9c0ee92ad6a9d074'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-4-see-my-usage-set-spend-caps-and-pause-on-failure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The shared pool only grows. Stale Listings stay visible, nothing bounds the free database, later epics have no way to pin or rank Listings safely, and the owner cannot remove a Listing or company on request (FR-8, NFR-7, AD-7).

**Approach:**
- Daily global jobs expire, purge, evict and check the quota.
- Small service hooks (`pin`, `unpin`, `set_relevance`) let later epics protect and rank Listings.
- An owner-run `block` removes matching Listings and keeps them from coming back, with an audit row.

## Boundaries & Constraints

**Always:**
- **Already in 0011:** `listings` already has `pin_count`, `relevance` and `expired_at`. Do NOT re-add them; the migration adds only what is new.
- **Config** (all in `core/config.py`, with `.env.example` and `render.yaml` entries):
  - `LISTING_UNSEEN_DAYS` (default 14, ≥ 7) and `LISTING_UNSEEN_RUNS` (default 3, ≥ 1).
  - `LISTING_PURGE_GRACE_DAYS` (default 30).
  - `LISTING_POOL_CAP` (default 30000).
  - `DB_QUOTA_BYTES` (default 500 MiB) and `DB_QUOTA_ALERT_PERCENT` (default 80).
- **Expire** (`listings.expire`, daily). A Listing with `expired_at IS NULL` is marked expired (`expired_at = now()`) when either holds:
  - `deadline < CURRENT_DATE`;
  - it was **not seen for a number of runs**: `last_seen_at` is older than `LISTING_UNSEEN_DAYS` AND at least `LISTING_UNSEEN_RUNS` provider runs for its Source (`provider_runs` with `status='succeeded'`, `finished_at` after the Listing's `last_seen_at`) exist. Without enough completed runs, nothing expires by unseen, so an idle pool never empties itself. Only Sources that collect through provider runs (`linkedin`) expire by unseen.
  - It is idempotent, batches its updates, and records an `ops_events` row with counts only.
- **Purge** (`listings.purge`, daily): deletes Listings with `expired_at < now() - LISTING_PURGE_GRACE_DAYS`, in batches, under `FOR UPDATE SKIP LOCKED`. It never deletes a pinned Listing (`pin_count > 0`). Deletes rely on each foreign key into `listings` declaring `ON DELETE CASCADE` or `SET NULL`. None exist yet; a convention test (like `test_owner_fk_convention.py`) fails if any FK into `app.listings` declares anything else, with a clear message.
- **Hooks** in `listings.service`, taking a session and never committing: `pin(session, listing_id)`, `unpin(session, listing_id)` (never below 0) and `set_relevance(session, listing_id, relevance: float)`.
  - `pin` also clears `expired_at`, so a pinned Listing stays visible to its tracker.
  - Each takes the row lock (`FOR UPDATE`), so it is safe against eviction and purge. An unknown id raises `NotFoundError`. `relevance` must be finite and within 0..100.
- **Evict** (`listings.evict`, daily): when active Listings (`expired_at IS NULL`) exceed `LISTING_POOL_CAP`, it deletes the excess in batches.
  - Order: lowest `relevance`, then oldest `last_seen_at`.
  - Only unpinned rows (`pin_count = 0`) are candidates, taken with `FOR UPDATE SKIP LOCKED`.
  - The delete itself re-checks `pin_count = 0`.
  - It records an `ops_events` row with the count removed, and never removes a pinned Listing, even when the cap cannot be reached.
- **Takedown** (`listings.service.block(session, kind, value, *, actor_id=None)` and `unblock(...)`):
  - New global table `listing_blocks` (owned by `listings`): `id`, `kind` (`listing`, `url` or `company`), `source` (set for `listing`), `value` (normalised), `created_at`, a unique constraint on `(kind, source, value)`, RLS. No `owner_id`.
  - Normalisation: `listing` is `source` + `source_job_id`; `url` is the canonical form with no query or fragment, lower-cased host; `company` is trimmed and case-folded.
  - Blocking deletes every matching Listing now (pinned ones too: a takedown wins), and writes an `admin_audit` row (`listings.block` or `listings.unblock`, `actor_id` the owner or NULL for the CLI) plus a counts-only `ops_events` row.
  - `ingest_draft` and `ingest_drafts` skip a blocked draft: it counts as rejected, is never stored, and is not an error. The check runs inside the same transaction as the upsert.
  - A new `api/scripts/block_listing.py` CLI (`block|unblock`, `--listing SOURCE:ID`, `--url`, `--company`) runs it, local and CI environments only unless `--yes-prod` is given.
- **Quota** (`listings.quota_check`, daily): reads `pg_database_size(current_database())`. At or above `DB_QUOTA_ALERT_PERCENT` percent of `DB_QUOTA_BYTES`, it records an `ops_events` row `listings.quota_alert` with counts (`used_mb`, `quota_mb`, `percent`) and logs a warning.
- **Scheduling:** one migration, `0015_listing_maintenance`:
  - creates `listing_blocks`;
  - inserts four global daily `schedules` rows (`listings.expire`, `listings.purge`, `listings.evict`, `listings.quota_check`; `owner_id` NULL) like 0013's reaper, staggered by 5 minutes at about 03:00 UTC;
  - adds a partial index for the purge scan (`expired_at` where not null) and one for eviction (`relevance, last_seen_at` where `expired_at IS NULL AND pin_count = 0`).
  - A task that raises is retried by the jobs layer. Every job is safe to repeat.
- Browse and detail already hide expired rows; they must also keep hiding them, and the web needs no change.

**Never:**
- Re-adding `pin_count`, `relevance` or `expired_at`.
- Deleting a pinned Listing in purge or eviction.
- Storing a description or raw provider output.
- A public API or UI for takedown (owner CLI only).
- Search (2.6).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Past deadline | `deadline` yesterday, `expired_at` NULL | expired by `listings.expire`; hidden from browse and detail | N/A |
| Deadline today | `deadline = CURRENT_DATE` | not expired | N/A |
| Unseen, enough runs | `last_seen_at` 20 days ago, 3 succeeded linkedin runs finished after it | expired | N/A |
| Unseen, too few runs | same, 1 succeeded run | not expired | N/A |
| Unseen, recent | `last_seen_at` 3 days ago | not expired | N/A |
| Manual Listing | `source='manual'`, old `last_seen_at` | not expired by unseen | N/A |
| Re-expire | run twice | second run changes nothing | N/A |
| Purge | `expired_at` 31 days ago / 10 days ago | the first is deleted, the second kept | N/A |
| Purge pinned | `expired_at` 31 days ago, `pin_count` 1 | kept | N/A |
| FK convention | an FK into `listings` with `RESTRICT` | the convention test fails | N/A |
| Pin / unpin | pin twice, unpin three times | count 2 then 0, never below 0; pin clears `expired_at` | unknown id: `NotFoundError` |
| set_relevance | 42.5; NaN; 150 | stored; `ValueError`; `ValueError` | N/A |
| Evict | cap 5, 8 active (2 pinned) | 3 lowest-relevance, oldest unpinned removed | N/A |
| Evict, all pinned | cap 2, 4 active, all pinned | nothing removed | N/A |
| Evict under cap | at or below cap | nothing removed | N/A |
| Concurrent pin | evict holds candidates while `pin` locks one | the locked row is skipped, not deleted | N/A |
| Block company | block "Acme Corp" | its Listings (any case) deleted, `admin_audit` row written | N/A |
| Block URL | URL with tracking query | matches the stored canonical URL | N/A |
| Block listing | `linkedin:123` | that Listing deleted | N/A |
| Never re-ingested | draft matching an active block | not stored; counted as rejected; others still ingest | N/A |
| Unblock | unblock a company | later ingests succeed; `admin_audit` row written | unknown block: no-op, returns False |
| Block pinned | pinned Listing matches | deleted anyway | N/A |
| Quota alert | size at 85% of quota | `listings.quota_alert` event | N/A |
| Quota fine | size at 50% | no event | N/A |
| Schedules | after `alembic upgrade head` | four global daily rows exist; `scheduler_tick` enqueues them | N/A |

</frozen-after-approval>

## Code Map

- `api/migrations/versions/0015_listing_maintenance.py` -- after `0014_spend_caps`. Model it on `0013_provider_runs.py` (the reaper schedule insert, `create_app_table_rls`). Bump the HEAD test. The owner-FK convention test must keep passing (`listing_blocks` has no `owner_id`).
- `api/app/modules/listings/models.py`, `repo.py`, `service.py`, `tasks.py` -- the `ListingBlock` model, batched expire/purge/evict SQL, the `block` check inside `_UPSERT`/`ingest_drafts` (read the existing per-draft savepoint and `rejected` counting), `pin`/`unpin`/`set_relevance`, and the five task entry points (`@task`, ids none, like `reap_stale_runs`).
- `api/app/core/ops.py` -- `record_event`, `record_audit` (`admin_audit` has actor and target account only; the action name carries the meaning).
- `api/app/core/config.py` -- new settings with validators. `.env.example` and `render.yaml` too.
- `api/scripts/block_listing.py` -- model on `scripts/promote_admin.py` for its environment guard and audit pattern.
- `api/tests/` -- `test_listing_expiry.py`, `test_listing_eviction.py`, `test_listing_takedown.py`, `test_listing_hooks.py`, `test_listing_fk_convention.py`, config tests. Follow `test_provider_runs.py` fixtures. Low-entropy fake ids and tokens only; GitGuardian scans every commit.
- `README.md` -- the daily jobs, caps and takedown CLI.

## Tasks & Acceptance

**Execution:**
- [x] Config and migration 0015 (blocks table, four schedule rows, indexes).
- [x] `listings` repo, service and tasks: expire, purge, evict, quota check, hooks, `block`/`unblock`, and the ingest block check.
- [x] `api/scripts/block_listing.py` and README.
- [x] Tests for every matrix row, plus the FK convention test.

**Acceptance Criteria:**
- Given a pool with expired, pinned, blocked and over-cap Listings, when the four daily jobs and a collection run, then browse shows only live Listings, pinned rows survive, blocked content never returns, and every job is idempotent.
- Given the full API suite, import-linter, the exposed-schema check, and the web suite, when they run, then everything passes.

## Design Notes

- **"Not seen for a number of runs"** has no direct counter, because collection is per Student and scoped by Preferences. The proxy is time plus completed runs: a Listing counts as missing only when enough runs have finished since it was last seen. An idle pool keeps its Listings. A Listing outside every Student's Preferences can still expire; that matches "no longer seen at the Source".
- **A takedown beats a pin.** Removing a Listing on request must work even when a tracker has pinned it; Epic 4's `SET NULL` on `applications.listing_id` keeps the tracker's snapshot.

## Implementation Notes

- **Batches use a MATERIALIZED CTE.** `DELETE ... WHERE id IN (SELECT ... LIMIT n FOR UPDATE SKIP LOCKED)` deleted more than `n` rows on Postgres 17 (the subquery re-ran per row), so purge, evict and expire select candidates in `WITH due AS MATERIALIZED (...)` and the delete re-checks `pin_count = 0`.
- **Unseen threshold.** `provider_runs` has no Source column, so `purpose = 'jobs'` stands for `linkedin`. A Listing has missed N runs when the Nth most recent succeeded run finished after its `last_seen_at`; this is one scalar subquery instead of a count per row. Seed runs count (they are provider runs for the Source).
- **Takedown ordering.** `block`/`unblock` take an exclusive advisory lock and every ingest a shared one (`pg_advisory_xact_lock[_shared]` on `listing_blocks`), so an ingest in flight finishes before a block deletes, and a later ingest sees the block. The check is a separate `EXISTS` in `_upsert`, inside the per-draft savepoint; a blocked draft raises an internal error that `ingest_drafts` counts as `rejected` (no warning) and `ingest_draft` turns into a `None` return.
- **Normalisation.** URL: lower-cased scheme and host (and port), no userinfo, query or fragment, and no trailing slash on the path (an addition to the spec so `/view/1/` and `/view/1` match). Company: `strip().casefold()`. Matching Listings are found by scanning `(id, url)` / `(id, company)` and comparing the normalised values in Python (exact, and fine for an owner CLI at about 30k rows). `block(session, "listing", "SOURCE:ID")` takes the Source and job id in one string. `block` returns the number of Listings deleted; `unblock` returns whether a block existed.
- **Events.** Every job always records its event, even with zero counts: `listings.expired` (`deadline`, `unseen`), `listings.purged` (`purged`), `listings.evicted` (`removed`, `active`, `cap`), `listings.quota_alert` (only on alert), `listings.blocked` (`removed`, `created`), `listings.unblocked`.
- **Schedules.** Ids `...0015` to `...0018`; `next_run_at` is the next 03:00 UTC plus 0/5/10/15 minutes, interval 24 hours.
- **Not run:** the web suite (no web change), ruff and mypy (the repo has no config; CI runs pytest, import-linter and the schema check).

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `listings.expire` re-marks a pinned Listing expired right after `pin` cleared it, hiding a tracker's Listing | high | `_EXPIRE_BATCH` due CTE has no `pin_count = 0`; reported by all three reviewers | patch |
| 2 | An idle pool still empties: the unseen test only needs the Nth newest succeeded run to be later than `last_seen_at`, but not recent; 3 runs on day 0 and silence expires every Listing older than 14 days | high | `threshold.cutoff` is any run time, so the design note "an idle pool keeps its Listings" does not hold | patch (the cutoff run must have finished within `LISTING_UNSEEN_DAYS`) |
| 3 | An over-long URL or company overflows `listing_blocks_value_check` and crashes the CLI; a stored URL that `urlsplit` rejects aborts a block mid-way; `set_relevance` leaks `OverflowError` for a huge int | medium | `_block_target` validates only emptiness; the URL branch has no per-row guard | patch |
| 4 | URL takedown misses the same job under a default port (`:443`, `:80`) | medium | `canonical_url` keeps the port | patch (drop default ports) |
| 5 | `block`/`unblock` wait on the global advisory lock with no timeout, stalling collection behind a queued exclusive request | medium | no `lock_timeout` set | patch (`SET LOCAL lock_timeout`, clear error, CLI exit 2) |
| 6 | `test_the_delete_rechecks_the_pin` does not exercise the DELETE's re-check and its `purge_batch` assertion is vacuous (the row is not expired) | medium | the candidate CTE already excludes pinned rows | patch (honest test) |
| 7 | The schedule migration test can leave the shared DB downgraded; the FK convention test ignores referencing tables outside `app` | low | no `try/finally`; `n.nspname = 'app'` filter on the referencing side | patch |
| 8 | Audit rows hold only the action name, and `unblock` deletes the block row, so the target is lost | low | `admin_audit` has actor and target account only (AD-18) | defer |
| 9 | Quota alert has no delivery or dedupe; no mass-expiry alert | low | `ops_events` is the designed channel for this phase | defer |
| 10 | CLI has no dry-run; no expire index; event can undercount after a retried partial job; evict reads the count once; a non-authorised re-sighting leaves a Listing expired | low | all are limited to an owner CLI or a small pool | defer |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres container with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run lint && npm run typecheck && npm test` -- expected: clean (no web change expected)
