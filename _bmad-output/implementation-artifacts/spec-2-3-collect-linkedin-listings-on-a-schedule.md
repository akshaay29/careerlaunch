---
title: 'Story 2.3: Collect LinkedIn Listings on a schedule'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: 'adaeb0790a90fd5e1ffd0d951888e4f9a8fab582'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The pool fills only from the local demo seed. Students with a Provider Key need LinkedIn Listings that match their Preferences collected regularly under their own Apify account. The owner also needs a seed run so the first Students never see an empty pool (FR-6, AD-4, AD-12).

**Approach:**
- One spend path, `listings.service.start_provider_run`, records a `provider_runs` row and starts an allowlisted Apify actor asynchronously.
- A self-rescheduling poll job fetches the results, maps them to `ListingDraft`s and ingests them through Story 2.1's pipeline.
- Saving a key creates or resumes a daily `listings.collect` schedule and enqueues a first run.
- No task waits synchronously on Apify, and no network call happens inside a DB transaction.

## Boundaries & Constraints

**Always:**
- **Actor check first.**
  - Before writing the mapping, confirm on Apify's store and API docs that `curious_coder/linkedin-jobs-scraper` (primary) and `harvestapi/linkedin-job-search` (fallback) still exist. Record each one's input fields (search keywords or URLs, location, job type, item limit) and output fields in Implementation Notes.
  - Never set a cookie, login or session input.
  - If neither actor exists or can be driven without cookies, stop and report instead of guessing.
- **Config:**
  - `APIFY_ACTOR_ALLOWLIST` (default: the two actors, in order).
  - `COLLECT_INTERVAL_HOURS` (default 24).
  - `COLLECT_MAX_ITEMS` (default 100).
  - `COLLECT_MAX_CHARGE_USD` (default 0.25).
  - `PROVIDER_RUNS_ENABLED` (default true; the platform kill switch).
  - `SEED_APIFY_TOKEN` (SecretStr, optional; used only by the seed script).
- **`ProviderRunner` additions (port, Apify adapter, fake), all taking `token: SecretStr`:**
  - `start_run(token, actor, run_input, *, max_items, max_charge_usd) -> str` calls `POST /v2/acts/{owner~name}/runs` with the `maxItems` and `maxTotalChargeUsd` query parameters and the integration header.
  - `get_run(token, run_id) -> RunState(status, usage_usd, dataset_id)`. The status is one of READY, RUNNING, SUCCEEDED, FAILED, TIMED-OUT, ABORTED.
  - `fetch_items(token, dataset_id, *, limit) -> list[dict]`.
  - Errors map as in Story 2.2: `ProviderKeyInvalid`, `ProviderUnavailable`, and a new `ProviderRejected` for 402/403 credit or plan errors. Story 2.4 turns those into pauses.
- **Apify `SourceAdapter`** (`adapters/apify/linkedin_source.py`):
  - `drafts_from_items(actor, items) -> (list[ListingDraft], rejected_count)`.
  - `source='linkedin'`, `source_job_id` = the LinkedIn job id from the item.
  - It maps title, company, location, job type, posted date, deadline when present, URL and description.
  - Invalid items are counted, not raised.
  - Raw items are never stored.
- **`provider_status` table** (`accounts`): `owner_id` PK (→ accounts CASCADE), `status` (`active` or `paused`), `reason`, `exhausted_until`, `updated_at`. A missing row means active. `accounts.service.get_provider_status`. Story 2.4 writes it.
- **`provider_runs` table** (`listings`):
  - `id`, `owner_id` (→ accounts CASCADE, NULL for the system seed), `purpose` (`'jobs'`), `run_key` (UNIQUE, e.g. `collect:<owner>:<local_date>` or `seed:<uuid>`), `actor`, `status`, `apify_run_id` (UNIQUE NULL).
  - Run details: `dataset_id`, `params jsonb` (role titles, locations, job types), `max_items`, `max_charge_usd`, `cost_usd`.
  - Counts: `items`, `ingested`, `rejected`.
  - Outcome: `error_kind`, `poll_attempts`, `started_at`, `finished_at`, `created_at`.
  - `status` is one of `starting`, `running`, `succeeded`, `failed` or `refused`.
  - RLS.
- **`listings.service.start_provider_run(owner_id, purpose, params, *, run_key)`** is the only caller of `ProviderRunner.start_run`.
  1. In a short transaction:
     - A row for `run_key` with an `apify_run_id` already set: no-op.
     - A `starting` row without one: mark it `failed` with `error_kind='start_unknown'`, and never restart it, so an Apify call that may have run is never paid for twice.
     - Otherwise check `require_consent(provider_runs)`, `provider_status` (active, and `exhausted_until` not in the future), `PROVIDER_RUNS_ENABLED`, `sources.enabled` for `linkedin`, and that a credential exists. Any failure inserts the row as `refused` with an `error_kind` and returns.
     - On success, insert the row as `starting` and commit.
  2. With no transaction open, decrypt the credential (`accounts.service.decrypt_credential`) and call `start_run` with the first allowlisted actor's mapped input, `COLLECT_MAX_ITEMS` and `COLLECT_MAX_CHARGE_USD`.
  3. In a new transaction, store the `apify_run_id`, set `running`, and enqueue `listings.poll_provider_run:provider_run:<id>` with `run_at` +60 s. A start error marks the row `failed` with an `error_kind` and records an `ops_events` row.
- **Poll job** `listings.poll_provider_run` (ids `provider_run_id`):
  - It calls `get_run` with no transaction open.
  - **READY or RUNNING:** increment `poll_attempts` and re-enqueue itself (same key, scoped by attempt) with backoff 60 s → 2 min → 4 min…, capped at 10 min. After 3 hours it marks the run `failed` with `timeout`.
  - **SUCCEEDED:** fetch items (limit = `max_items`) and map them with the adapter. Then, in one transaction, call `listings.service.ingest_drafts(session, drafts, collected_by=owner_id)` — a new helper that shares Story 2.1's per-draft savepoint, consent, visibility and batched-event logic, and that `collect_and_ingest` also reuses. Record `cost_usd` (`usage_usd`), the item, ingested and rejected counts, `succeeded`, `finished_at`, and an `ops_events` row `listings.provider_run_succeeded` with counts only.
  - **FAILED, ABORTED or TIMED-OUT:** mark the run `failed` with `error_kind` = the status and record cost and an `ops_events` row. Don't start a replacement run: the next scheduled run collects again, which avoids extra spend.
  - **Transient errors** (`ProviderUnavailable`) raise, so the job's retry policy backs off.
- **Scheduling:**
  - A `listings` subscriber to `ProviderKeyChanged(removed=False)` upserts the owner's `schedules` row (`kind='listings.collect'`, `owner_id`, `paused=false`, `next_run_at = now + COLLECT_INTERVAL_HOURS`). Resuming here is safe because every start re-checks consent and `provider_status`. This settles the Story 2.2 deferral.
  - The same subscriber enqueues a first `listings.collect` for today, inside the same transaction.
  - `listings.collect` (ids `owner_id`, key `listings.collect:student:<owner>:<local_date>`, where `local_date` comes from the Student's Preferences time zone, default Asia/Kolkata, via a new `core.clock.local_day(tz)`) loads the owner's Preferences.
    - With no role titles it records `listings.collect_skipped` (`no_preferences`) and returns.
    - Otherwise it calls `start_provider_run(owner_id, 'jobs', params, run_key=f"collect:{owner}:{local_date}")`.
  - `scheduler_tick` already enqueues due schedules.
- **Seed:** `api/scripts/seed_pool.py` refuses to run without `SEED_APIFY_TOKEN`. It starts a run under that token (`owner_id` NULL, contributing to the pool, `run_key=seed:<uuid>`) with role titles and locations given as CLI args. It polls with sleeps in the script process, since a CLI may wait, and ingests through the same code. It prints counts and cost.
- **Privacy:** tokens never appear in logs or rows; descriptions are never stored (Story 2.1 rules hold).

**Never:**
- Waiting on Apify inside a request or a job's transaction, or calling the network inside a DB transaction.
- Starting a second run for a `run_key`, or automatically re-running a failed run.
- Usage UI, caps, or pause-on-failure handling (Story 2.4); expiry or eviction (Story 2.5).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Key saved | `ProviderKeyChanged(removed=False)` | schedule row upserted, unpaused; one `listings.collect` job enqueued for today | N/A |
| Tick due | due schedule | `listings.collect:student:<id>:<date>` enqueued | N/A |
| Collect | Preferences with role titles, locations, job types | `start_provider_run` called with those params; row `running` with the Apify run id; poll enqueued | N/A |
| No Preferences | no role titles | `collect_skipped` ops event; no row | N/A |
| Refused | no consent / status paused / exhausted / disabled / Source disabled / no key | row `refused` with `error_kind`; runner not called | N/A |
| Same key twice | collect runs again for the same date | no second Apify start | N/A |
| Crash after start | `starting` row without a run id, collect retried | row marked `failed start_unknown`; no new Apify start | N/A |
| Running | poll sees RUNNING | re-enqueued with backoff; `poll_attempts`+1 | N/A |
| Success | poll sees SUCCEEDED with a fake dataset of 10 items (2 invalid) | 8 Listings ingested (visibility per consent); `cost_usd`, counts and `succeeded` recorded; ops event | N/A |
| Actor failed | FAILED | row `failed` with `error_kind`; ops event; no re-run | N/A |
| Transient | `get_run` raises unavailable | job raises (retried); row unchanged | N/A |
| Timeout | still running after 3 h | `failed timeout` | N/A |
| Adapter mapping | real-shaped items from each actor (fixtures taken from the docs) | correct drafts; bad items counted | N/A |
| Adapter HTTP | MockTransport: start/get/items requests | correct URLs, query params (`maxItems`, `maxTotalChargeUsd`), integration header; no cookie fields in the input | N/A |
| Seed | no `SEED_APIFY_TOKEN` | refuses, exit code 2 | N/A |
| Account deleted | owner removed | `provider_runs` and `provider_status` rows cascade | N/A |

</frozen-after-approval>

## Code Map

Builds on Epic 1 and Stories 2.1–2.2. Reuse them; don't restructure.
- `api/app/ports/provider_runner.py`, `api/app/adapters/apify/runner.py`, `api/tests/fakes/provider_runner.py` -- the Story 2.2 port, adapter (`validate_token`, `_request`-style error mapping, integration header, https base) and fake; extend all three.
- `api/app/ports/source_adapter.py` -- `ListingDraft`, `SourceQuery`.
- `api/app/modules/listings/service.py` -- `ingest_draft`, `collect_and_ingest` (per-draft savepoint, consent read, visibility, batched `ListingsIngested`, ops events). Extract `ingest_drafts`.
- `api/app/modules/accounts/service.py` -- `require_consent`, `decrypt_credential` (returns a `SecretStr`; may raise `ProviderKeyUnreadableError`), `COLLECTION_SCHEDULE_KINDS`, the `ProviderKeyChanged` publisher. `accounts` can't import `listings`, so the subscriber lives in `listings/events.py`, registered at app wiring.
- `api/app/modules/profile/service.py` -- `get_preferences` (role titles, locations, job types, time zone).
- `api/app/core/jobs.py` -- `task`, `enqueue(run_at=…)`, `RetryPolicy`, `scheduler_tick` (`schedules.kind` = task name), `pause_owner_schedules`.
- `api/app/core/ops.py` -- `record_event`.
- `api/migrations/versions/0012_provider_credentials.py` -- latest migration; the new one is `0013_provider_runs.py` (both tables). Bump `HEAD`. The owner-FK convention test must pass.
- Mark every fake token in tests with `# ggignore -- fake test token` before committing. GitGuardian scans every commit.

## Tasks & Acceptance

**Execution:**
- [x] Verify the actors (docs) → Implementation Notes. Then config settings plus `.env.example`/`render.yaml`.
- [x] `ProviderRunner` start/get/items in the port, adapter and fake. `adapters/apify/linkedin_source.py` mapping, with fixtures.
- [x] `api/app/core/clock.py` -- `local_day(tz)`.
- [x] `api/migrations/versions/0013_provider_runs.py` -- `provider_status` and `provider_runs`.
- [x] `accounts` -- the `provider_status` model, repo and `get_provider_status`.
- [x] `listings` -- the `provider_runs` model and repo, `start_provider_run`, `ingest_drafts` (refactor `collect_and_ingest` onto it), the `collect` and `poll_provider_run` tasks, and the `ProviderKeyChanged` subscriber.
- [x] `api/scripts/seed_pool.py`, `README.md` -- the seed and how collection works (cadence, costs, kill switch).
- [x] `api/tests/test_provider_runs.py`, `test_collection_schedule.py`, `test_apify_linkedin_source.py`, adapter tests -- every matrix row.

**Acceptance Criteria:**
- Given the fake runner and a fake dataset, when a key is saved and the drains run (collect, then poll), then Listings appear in `GET /api/v1/listings` for that Student, and no test needs real Apify.
- Given the full suite, when it runs, then every earlier test still passes, including Story 2.1's `collect_and_ingest` tests after the refactor.

## Design Notes

- **A failed actor run isn't retried automatically.** Each start spends the Student's credit; the next daily schedule is the retry. Only transient API errors (polling, network) retry within the job.
- **`run_key` per owner and local day** makes collection idempotent even when the scheduler and a key save both enqueue on the same day.
- **Starting without a run id is treated as unknown, not restartable.** It's better to miss a day than to pay twice.

## Implementation Notes

**Actor check (2026-11-14, Apify Store pages plus each actor's build `openapi.json` input schema).** Both actors exist and run without cookies.
- `curious_coder/linkedin-jobs-scraper` (primary, pay per result, from about USD 1 per 1,000 results).
  - Input: `urls` (public LinkedIn job-search URLs; when set, the AI filters are ignored), `keywords`, `location`, `geoId`, `distance`, `datePosted` (`anyTime|past24Hours|pastWeek|pastMonth`), `companyIds`, `under10Applicants`, `autoConvertToAiSearch` (default true: LinkedIn's AI search dropped classic filters such as job type, which are converted to search terms), `scrapeCompany`, `limitPerSource` (the item limit per search), `splitByLocation`, `splitCountry`. It has no job-type field and no cookie, login or session field.
  - We send one URL per role title and location (at most 10), with `keywords`, `location`, `f_TPR=r604800` (last week) and `f_JT` (I and F); `limitPerSource = ceil(max_items / searches)`, `scrapeCompany=false`, `splitByLocation=false` and `autoConvertToAiSearch=true`.
  - Output fields: `id`, `link`, `title`, `companyName`, `location` (text), `postedAt` (`YYYY-MM-DD`), `postedAtTimestamp`, `expireAt` (when available), `employmentType` (`Internship`, `Full-time`, `Contract`…), `descriptionText` and `descriptionHtml`, plus salary, applicants, poster and company details (never mapped).
- `harvestapi/linkedin-job-search` (fallback, pay per event, about USD 1 per 1,000 jobs).
  - Input: `jobTitles` (array), `locations`, `maxItems` (per title and location pair), `employmentType` (enum `full-time`, `part-time`, `contract`, `internship`, `temporary`), `experienceLevel`, `workplaceType`, `postedLimit` (`1h|24h|week|month`), `sortBy`, `company`, `salary`, `geoIds`, `page`, `industryIds`, `functionIds`, `easyApply`, `under10Applicants`, `cookie` (optional, NEVER set), `userAgent`, `proxy`.
  - We send `jobTitles`, `locations`, `maxItems`, `employmentType` (`internship`, `full-time`), `postedLimit=week` and `sortBy=date`.
  - Output fields: `id`, `linkedinUrl`, `title`, `company.name`, `location.linkedinText` / `location.parsed.text`, `employmentType` (`full_time`…), `postedDate` (ISO), `expireAt` (ISO), `descriptionText`.
- **Mapping for both actors:** `source_job_id` is the numeric LinkedIn job id, and the URL is the canonical `https://www.linkedin.com/jobs/view/<id>/`, with no tracking parameters. `expireAt` is LinkedIn's display expiry, not an application deadline, so it is NOT mapped: `deadline` would come only from an explicit application-deadline field, which neither actor provides, so it is always `None` today. Story 2.5 expires Listings by not-seen counts. Text is capped to the `ListingDraft` limits instead of being rejected. Unparseable dates become null. An item without a numeric id or a title, or one that fails validation, is counted as rejected. A job id repeated within one dataset is kept once.

**Design choices and deviations:**
- `start_provider_run(owner_id, purpose, params, *, run_key, token=None, max_items=None)` and `poll_provider_run(run_id, *, token=None, reschedule=True)` open and commit their own short transactions. The spec requires a commit before each network call, so this is a deliberate exception to "services never commit". Only the seed passes `token`, and only with `owner_id=None`.
- **Review fixes (round 1):**
  - A `starting` row without a run id is another start in flight while younger than 10 minutes (a no-op), and a crash after that (`start_unknown`). Step 3 locks the row and sets `running` only if it is still `starting` with no run id. Otherwise the new Apify run is aborted (best effort) and `listings.provider_run_orphaned` is recorded.
  - `listings.reap_stale_runs` runs hourly from a global schedule row inserted by migration 0013. It fails `starting` rows older than 15 minutes (`start_unknown`) and `running` rows started more than `RUN_TIMEOUT` + 1 hour ago (`poll_exhausted`, after a best-effort abort).
  - `ProviderRunner.abort_run` (`POST /v2/actor-runs/{id}/abort`) is idempotent; the adapter swallows and logs its errors. It is called on `timeout`, on reaped running runs and on orphaned starts.
  - Start step 2 fails any unexpected exception as `start_error`, logging the class name only. The poll fails `credentials_unavailable` when no master key is set. RUNNING polls store the cost so far, so a later `no_key` or `key_unreadable` failure keeps the last known cost.
  - Cleaned params with no role titles are refused as `no_preferences`.
  - Every allowlisted actor must have a mapping (`SUPPORTED_ACTORS`), checked in `app.main` wiring at startup.
  - `COLLECT_INTERVAL_HOURS` must be at least 24. The next run is anchored to `COLLECT_LOCAL_HOUR` (default 6) in the Student's time zone (`core.clock.next_local_time`). Re-saving a key sets `next_run_at` only for a new or paused schedule.
  - The harvestapi input caps its titles and locations with the same choice as the primary actor's searches, so the run makes at most `MAX_SEARCHES` searches.
  - A recursive credential guard (`check_no_credentials`) raises `ValueError` on credential-like keys at any depth and on credential-like query parameters in generated URLs.
  - New `duplicates` and `not_visible` columns, so that `items = ingested + rejected + duplicates + not_visible`.
  - The seed takes `--max-charge-usd` (up to 2), warns when the item limit exceeds what the charge cap buys, and parses its arguments before checking the token.
- A `refused` row is checked again by a later call with the same `run_key`, for example after the Student grants consent on the same day, and is updated in place. `failed` and `succeeded` rows are never restarted. Concurrent callers are serialised with `pg_advisory_xact_lock(hashtextextended(run_key))`.
- `error_kind` values:
  - Refusals: `no_consent`, `paused`, `exhausted`, `disabled`, `source_disabled`, `no_key`, `no_actor`.
  - Start errors: `start_unknown`, `key_invalid`, `rejected`, `unavailable`, `bad_input`, `key_unreadable`, `credentials_unavailable`, `no_key`.
  - Poll outcomes: `failed`, `aborted`, `timed_out` (lowercased Apify status), `timeout` (our 3-hour limit), `no_dataset`, `key_invalid`, `rejected`, `no_key`, `key_unreadable`.
  - Each error records an `ops_events` row whose counts are `{error_kind: 1}`.
- Run calls map 402 and 403 to `ProviderRejected`. Token validation keeps the Story 2.2 mapping. Apify's transitional `TIMING-OUT` and `ABORTING` statuses are read as `RUNNING`.
- The mapping sits behind a new `ProviderSource` port in `ports/source_adapter.py`, installed by `app.main`, because `listings` can't import `adapters`. `ApifyLinkedInSource` provides `run_input` and `drafts_from_items`.
- The schedule upsert is a new helper, `core.jobs.upsert_owner_schedule`, because `core` owns `schedules`.
- The poll job retries transient errors with `RetryPolicy(max_attempts=8, exponential_wait=4)`, about 6 hours, which outlasts the 3-hour run window. A failed actor run is never re-run.
- The seed run (`owner_id` NULL) enqueues no poll job. The script polls it with the seed token, which is never in Render.
- `scheduler_tick` is unchanged. A due schedule is enqueued with its generic key, `listings.collect:owner:<id>:<UTC slot>`, not `…:student:<id>:<local_date>`. The per-day `run_key` (`collect:<owner>:<local_date>`) is what makes the tick and a key save on the same day start only one Apify run.
- `listings.collect` without role titles, or without a Preferences row, records `listings.collect_skipped` (`no_preferences`).
- Story 2.2's test helper `_add_schedule` (in `test_provider_key.py`) now upserts, since saving a key creates the `listings.collect` row.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Concurrent starts for one `run_key` (tick job and key-save job have different job keys) can mark an in-flight start as `failed/start_unknown`, and then step 3 silently overwrites it to `running` | high | Step 1 releases the lock before Apify is called; step 3's `update_run` doesn't check the current status | patch |
| 2 | Runs can get stuck: `running` once `POLL_RETRY` is exhausted or a non-transient error escapes (`CredentialsUnavailableError` in the poll); `starting` if step 3 fails or a non-Provider exception escapes step 2. Nothing sweeps them | high | No reaper; the poll catches only `ProviderKeyUnreadableError`; step 2 catches `ProviderError` and `ValueError` only | patch |
| 3 | Local timeout and key failure mark the row failed, but the Apify actor keeps running and billing; there is no `abort_run` | medium | `ProviderRunner` has no abort; `timeout`/`no_key`/`key_unreadable` paths | patch |
| 4 | Wiring and failure paths untested: `create_app` provider-source wiring; the start branches `key_unreadable`, `credentials_unavailable` and `bad_input`; the poll branches for fetch errors (no fake hook), `no_dataset` and `key_unreadable`; the `POLL_RETRY` budget vs `RUN_TIMEOUT` | medium | Gaps filed pre-verified | patch |
| 5 | The harvestapi input ignores `MAX_SEARCHES` (sends full title and location lists, so the cross product runs) | medium | `_harvestapi_input` dedupes the lists separately | patch |
| 6 | The actor allowlist isn't validated against the supported mappings, so a typo makes every daily run fail as `bad_input` quietly | medium | `_actor_names` checks the format only | patch |
| 7 | Schedule not anchored to the local day: drift or DST can put two ticks on one local date and skip a day; `COLLECT_INTERVAL_HOURS` < 24 silently no-ops; re-saving a key pushes `next_run_at` back each time | medium | `next_run_at = now + interval`; `run_key` per local day; the subscriber resets `next_run_at` | patch |
| 8 | Whitespace-only role titles clean down to `[]` and an empty, billed run starts | medium | `_clean_params`, then `run_input` with an empty search list | patch |
| 9 | `expireAt` is mapped to `deadline`, but it's LinkedIn's display expiry, not an application deadline | medium | `linkedin_source` mapping; `deadline` will drive UI and expiry | patch |
| 10 | The credential guard is an `assert` (stripped under `-O`), top-level keys only; bare asserts for invariants in the service and seed | low | `run_input` guard; direct fix to an explicit recursive check and raises | patch |
| 11 | `maxTotalChargeUsd` is formatted with `:g` (gives `1e-05`); seed `--max-items` up to 1000 against a fixed 0.25 cap; seed `--help` needs the token; seed asserts on a missing row | low | Direct fixes (fixed decimal, a `--max-charge-usd` option, parse args first) | patch |
| 12 | Run counts don't reconcile (`items != ingested + rejected`); `RunOutcome` drops `error_kind` on early returns; `_ACTOR_RE` is duplicated | low | Add `duplicates` and `not_visible` counts; direct fixes | patch |
| 13 | Account deletion cascades away cost history and leaves an in-flight Apify run going | medium | `provider_runs.owner_id` CASCADE; no abort on deletion. Cost history retention is a Story 2.4/AD-17 decision | defer |
| 14 | `scheduler_tick` enqueues `listings.collect:owner:<id>:<slot>`, not the matrix's `listings.collect:student:<id>:<local_date>` | low | `scheduler_tick` is generic (`kind` = task name) and keys by schedule slot; the per-local-day `run_key` in `provider_runs` (unique) guarantees one Apify start per Student per day, which is the row's intent. Documented in README | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff.

**Manual checks:**
- With the owner's real Apify token (`SEED_APIFY_TOKEN`), run `seed_pool.py` with one role title and location and a small item limit, then check the pool and the recorded cost. This runs only when the owner provides a token.
