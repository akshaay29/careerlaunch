---
title: 'Story 4.4: Launch hardening'
type: 'feature'
created: '2026-11-25'
status: 'done'
baseline_commit: 'a1dbc44c033d75a58ef2f49e27cbc90306afdcd3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The MVP works, but before real Students arrive nothing keeps job and ops history from growing forever, nothing proves every endpoint is isolated per Student, the performance checks are flaky on loaded machines, and there is no checked go-live procedure (NFR-1, NFR-2, NFR-7, NFR-8).

**Approach:**
- A daily retention job.
- A CI isolation suite that refuses to pass when a new id-taking route has no isolation test.
- A database-role check that `anon` and `authenticated` can read nothing.
- Load-robust performance checks.
- A per-Student cap on manual adds.
- A go-live checklist with a script that verifies everything a machine can check.

## Boundaries & Constraints

**Always:**
- **Retention** (`core.purge_history`, a daily global `schedules` row added by migration `0021_retention`, around 03:30 UTC). It is idempotent and runs in batches of at most 1,000 rows per statement, looping until done. It deletes:
  - Procrastinate jobs in a finished state (`succeeded`, `failed`, `cancelled`, `aborted`) whose last event is more than 7 days old, together with their events (by FK, or explicitly when there is no cascade);
  - `job_outbox` rows older than 7 days (they should have been flushed long ago; this is a leak guard);
  - `ops_events` older than 90 days.

  It never deletes `todo` or `doing` jobs, `admin_audit` rows, or anything newer. It records one counts-only `core.purge_history` ops event (the counts per table). Config: `JOB_HISTORY_RETENTION_DAYS` (7) and `OPS_EVENTS_RETENTION_DAYS` (90), both at least 1.
- **Isolation suite** (`api/tests/test_isolation.py`):
  - **Enumeration:** list every route from `app.routes` whose path has a parameter (for example `{listing_id}`, `{application_id}`, `{resume_id}`, `{document_id}`, `{id}`) and every route under the Student-owned prefixes.
  - **Registry:** a registry maps each `(method, path)` to a factory that creates the resource for Student A.
    - The test asserts Student B gets **404** (not 403, not 200) for A's id on every such route, and gets nothing of A's from the list routes.
    - Read-only routes over shared pool data (for example `GET /listings/{id}` for a pool Listing) are marked `shared` in the registry with a reason. For those, the test uses A's **private** Listing instead.
  - **Completeness guard:** a meta-test fails with the route's name when a route with a path parameter is neither in the registry nor in an explicit exemption list. Every exemption carries a reason.
  - **Admin routes:** every route that requires the admin role returns **403** to a signed-in Student (enumerated by dependency or prefix). There is also an equivalent completeness guard.
- **Anon / authenticated database check** (`api/tests/test_db_roles.py`, plus the same check added to `scripts/check_exposed_schema.py`): for every table in schema `app` (enumerated from `pg_tables`), `SET LOCAL ROLE anon` and then `authenticated`, run `SELECT 1 FROM app.<table> LIMIT 1` and assert a permission error. Run `SELECT 1 FROM public.<anything>` the same way. This stands in for "an anon REST request to each table is refused", since CI has no PostgREST.
- **Performance checks:**
  - `test_listing_search_perf.py` and `test_feed_perf.py` enforce the strict budgets (500 ms search, 2 s feed) only when `CI=true` or `PERF_STRICT=1`.
  - Otherwise they measure a calibration query, a trivial indexed `SELECT` on the same seeded database, and assert the budget multiplied by the local slowdown factor (local calibration ÷ a recorded CI reference time, clamped to 1–4×). They always print the timings.
  - The seeded sizes stay at about 30,000 Listings and 500 scores per Student, as the epic specifies. A test proves the strict branch is taken when `CI=true`.
- **Manual-add cap** (the 4.3 deferral): a Student may hold at most `MANUAL_LISTINGS_MAX` (config, default 200) manual Listings. Adding beyond that is a 422 with the message "You've reached the limit of 200 added jobs. Delete some to add more." Reusing an existing link is never blocked.
- **Go-live checklist** (`docs/go-live.md`) and `api/scripts/go_live_check.py` (reads `DATABASE_URL` and `API_BASE_URL`, read-only, prints PASS, FAIL or MANUAL lines, exits 1 on any FAIL). It checks automatically that:
  - the pool has at least `--min-pool` (default 50) active pool Listings;
  - the `pg_cron` drain job exists and ran successfully in the last 30 minutes (from `cron.job_run_details`, when the `cron` schema exists; otherwise MANUAL);
  - `GET /health` returns 200;
  - migrations are at HEAD;
  - `check_exposed_schema` passes;
  - the four daily maintenance schedules and the retention schedule exist and are not paused.

  It prints as MANUAL steps, with exact instructions:
  - the latest GitHub Actions health-check run is green (`gh run list --workflow health-drain.yml`);
  - the credentials master key has an offline copy;
  - Render free instance hours stay within the limit;
  - a `pg_dump` backup was taken, with the command;
  - production secrets are set: `CREDENTIALS_MASTER_KEYS`, `SUPABASE_SERVICE_ROLE_KEY` in Render and in the GitHub `production` environment, and `SEED_APIFY_TOKEN` for the seed run;
  - the seed run (`scripts/seed_pool.py`).

  The README links to `docs/go-live.md`.

**Never:**
- Deleting `admin_audit`, Student data, or unfinished jobs.
- Running the go-live script against production from CI.
- Weakening a budget in CI.
- Production secrets in the repo.
- New product features.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Retention | succeeded job 8 days old, failed job 3 days old, `todo` job 30 days old | 8-day job and its events deleted; the 3-day and `todo` jobs kept | N/A |
| Outbox | outbox rows 8 days and 1 day old | the 8-day row deleted | N/A |
| Ops events | 91-day and 89-day ops events; an `admin_audit` row 2 years old | 91-day event deleted; 89-day event and audit row kept | N/A |
| Idempotent | run twice | second run deletes nothing; counts event recorded | N/A |
| Batched | 2,500 old ops events | all deleted in batches of 1,000 or fewer | N/A |
| Schedule | after `alembic upgrade head` | the global daily retention row exists and `scheduler_tick` enqueues it | N/A |
| Isolation | every id route | Student B gets 404 for A's ids; list routes return nothing of A's | N/A |
| Completeness | a new id route without a registry entry | the meta-test fails, naming it | N/A |
| Admin | every admin route as a Student | 403 | N/A |
| DB roles | `anon` / `authenticated` select from each `app` table | permission denied for every table | N/A |
| Perf in CI | `CI=true` | strict 500 ms / 2 s budgets | N/A |
| Perf locally | slow machine | budget scaled by the calibration factor (at most 4×); timings printed | N/A |
| Manual cap | 200 manual Listings, add a 201st | 422 with the limit message | re-adding an existing link still returns 200 |
| Go-live script | local stack with seed data | PASS lines for the automatic checks; MANUAL lines with instructions; exit 0 | a missing schedule or a failing health check: FAIL, exit 1 |

</frozen-after-approval>

## Code Map

- `api/app/core/jobs.py` -- `task`, the global schedule pattern (reaper and maintenance rows in migrations 0013 and 0015), `scheduler_tick`. The Procrastinate tables and their status enum: check the vendored schema migration (`0002_jobs.py`) for table names, the events table and FK cascades. `job_outbox` columns.
- `api/app/core/ops.py` -- `record_event`; `ops_events` columns (`created_at`).
- `api/migrations/versions/0021_retention.py` -- after `0020_manual_listings`; the schedule row; bump HEAD.
- `api/app/main.py` -- route list and admin dependencies (`require_admin` in `core.auth`); the routers per module.
- `api/tests/` -- existing factories: `tracker_fixtures.py`, `match_fixtures.py`, `listing_fixtures.py`, profile and documents tests (Resume and Document uploads with the fake file store), `test_account_deletion.py`; `tests/sql/supabase_roles.sql` (anon and authenticated roles).
- `api/scripts/check_exposed_schema.py` -- extend with the per-table role check.
- `api/tests/test_listing_search_perf.py`, `test_feed_perf.py` -- current budgets and seed SQL.
- `api/app/modules/listings/service.py` `create_manual_listing` -- the manual-add cap.
- `README.md`, new `docs/go-live.md`, `api/scripts/go_live_check.py`, `.github/workflows/health-drain.yml` (reference for the health check).
- Test Postgres at 127.0.0.1 with `timezone=UTC`; also run the full suite once with `localhost` (as CI does). Low-entropy fake ids only; no real secrets anywhere.

## Tasks & Acceptance

**Execution:**
- [x] Config; migration 0021; `core.purge_history` task and tests.
- [x] `test_isolation.py` with registry, completeness guards and admin checks; `test_db_roles.py` and the extended exposed-schema check.
- [x] Load-robust perf tests; manual-add cap; `docs/go-live.md`, `scripts/go_live_check.py` (tested against the test DB with a fake health endpoint), README link.

**Acceptance Criteria:**
- Given CI, when the suite runs, then the isolation suite covers every id route (completeness guard green), the DB role check refuses `anon` and `authenticated` on every `app` table, the perf checks run with strict budgets, and retention is tested.
- Given the full API suite (run once with `localhost` and once with `127.0.0.1`), import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Completeness beats coverage.** A hand-written isolation test goes stale the day someone adds a route. Enumerating routes and failing on unknown ones keeps the guarantee as the API grows.
- **Calibrated local budgets.** CI is the authority on performance. Locally, a calibration query separates "this machine is busy" from "the query got slower", so the check stays useful instead of flaky.

## Implementation Notes

- **Retention** (`app/core/retention.py`, task `core.purge_history`, in `TASK_MODULES`): each batch is its own committed `DELETE ... WHERE id IN (SELECT ... LIMIT 1000)`; counts accumulate per batch. A finished job is deleted only when it has at least one event and every event time is set and older than the cutoff (no events or a NULL time keeps it); events go by the FK cascade. Before old outbox rows are deleted, a WARNING is logged and a `core.outbox_dropped` ops event records rows per task (key `module__task`), `total` and `oldest_age_hours`. A failing step still records `core.purge_history` with the partial counts plus `error: 1`, then re-raises. A subprocess test asserts every global schedule kind from migrations 0013, 0015 and 0021 is a registered task.
- **Isolation**: routes are enumerated with FastAPI's public `iter_route_contexts` (0.162 wraps included routers). Guards: id routes (registry or reasoned exemption), Student write routes without an id (`WRITE_ROUTES`), admin routes (403), and every non-public `/api/v1` route returns 401 without a token. B's responses are checked for A's ids and A's distinctive values (title, filenames, notes, description, stored score), with positive controls on A's side. `GET /applications/by-listing/{listing_id}` is `scoped` (always 200 with the caller's own id or null). The consent `{purpose}` routes are exempt (an enum). The body-id PATCH with A's `resume_id` is 422 "No such Resume version." by the Story 4.1 contract, identical to an unknown id (asserted), so B learns nothing; 404 would wrongly say B's own Application is missing.
- **Role probe** (`scripts/check_exposed_schema.py`, inside `find_violations`): every `app` relation with relkind r/p/v/m/f is read as `anon` and `authenticated` in a rolled-back savepoint; anything but SQLSTATE 42501 is a violation naming the outcome. `SET ROLE` checks the session user's membership, so the "cannot SET ROLE" test switches `SET LOCAL SESSION AUTHORIZATION` to a non-member role.
- **Perf**: `tests/perf_budget.py`. `CI` = true/1/yes (any case) or `PERF_STRICT=1` is strict. The calibration (best of 5 x 50 PK lookups) is measured before and after the timed work (slower counts) and printed as `perf calibration: <s> s`. `CI_REFERENCE_SECONDS = 0.025` is provisional: set it from a CI log.
- **Manual cap**: counts only the owner's manual Listings that have an Application, read as `pin_count > 0` (only the owner's own Application pins a manual Listing), so `listings` never reads `tracker`'s table (AD-2). Checked under the per-owner advisory lock after the reuse check; the orphan-replacement path (`reuse_existing=False`) is never capped, and `manual_urls` now lists pinned matches first so a re-add finds the tracked Listing before an orphan. 422 with `loc ["body"]` (the web form shows it as the form error); the OpenAPI 422 description changed, so the web client was regenerated.
- **Go-live script**: migrations check first; each check runs in its own savepoint and any database error, missing table, missing `cron` privilege or request exception (InvalidURL included) becomes a FAIL line with a short reason. Added: schedules more than 2 hours overdue, outbox rows older than 15 minutes, `todo` jobs due more than 1 hour ago, and the pg_net `net._http_response` 200 check (MANUAL note when the table is absent). Health is `GET /api/v1/public/health` (where `/health` is mounted). Run with `MIGRATION_DATABASE_URL`. The backup dumps `app` and `auth`; restore notes are in `docs/go-live.md`.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Nothing proves `core.purge_history` is registered in a process that doesn't import `app.core.retention`; if not, the first tick pauses the schedule and retention never runs | high | tests import the module directly | patch (subprocess check that every global schedule kind registers) |
| 2 | The outbox purge silently deletes queued work that never ran (e.g. after a week-long drain outage) | high | `_DELETE_OUTBOX` | patch (record task names, counts and oldest age in a separate ops event and a warning before deleting) |
| 3 | A failing step leaves committed deletions without any ops event | medium | event written only at the end | patch (try/finally with partial counts and an error flag) |
| 4 | A finished job with no events, or NULL event times, is deleted at any age | medium | `NOT EXISTS ... e.at >= :cutoff` | patch (require a known event older than the cutoff) |
| 5 | The manual-add cap counts orphan manual Listings and blocks re-adding a link already added | high | `count_manual`; `reuse_existing=False` path | patch (count only manual Listings with an Application) |
| 6 | The go-live script crashes on DB errors, a missing table, a bad `API_BASE_URL`, or a `cron` schema without privileges; schedules can be stale; no outbox backlog check; the drain cron check proves only that SQL ran | medium | `go_live_check.py` | patch |
| 7 | The exposed-schema role probe ignores views and matviews, accepts unexpected SQLSTATEs, and its "cannot SET ROLE" guard is untested; the docs don't name the URL to use | medium | `role_read_violations` | patch |
| 8 | Isolation suite: only ids are forbidden (distinctive strings could leak), the body-id test accepts any 4xx, no 401 completeness guard | medium | `test_isolation.py` | patch |
| 9 | The go-live pool test never includes manual or private Listings | low | `_ready` inserts only pool rows | patch |
| 10 | `CI=1` or `CI=yes` isn't treated as CI | low | `perf_budget.py` | patch |
| 11 | The backup command lacks restore notes (`auth` objects owned by Supabase) | low | docs | patch |
| 12 | `CI_REFERENCE_SECONDS` is a guess | low | no CI measurement yet | patch after the first CI run (read the printed calibration) |
| 13 | The jobs purge re-scans finished jobs each batch; no index on `procrastinate_events (job_id, at)` confirmed | low | retention SQL | defer |
| 14 | The retention migration test downgrades the shared DB mid-suite | low | the suite doesn't run in parallel | reject |
| 15 | No web test for the cap's 422 on the form | low | no web harness | defer |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with `timezone=UTC`; run once via `localhost`, once via `127.0.0.1`)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean
