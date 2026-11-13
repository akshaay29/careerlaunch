---
title: 'Story 1.2: Background jobs that survive a sleeping server'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: 'ff769bec6d2323d057fa445dc2e8909519028b8b'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The API runs on a free host that sleeps, with no always-on worker. Later stories (Resume extraction, ingestion, scoring, purges) need background work that is never lost or committed without its state change, and that keeps running even when nothing is awake.

**Approach:** Add a transactional job outbox, Procrastinate installed in schema `app`, a `schedules` table with `scheduler_tick`, an append-only `ops_events` log, and a secret-protected drain endpoint. pg_cron calls the drain every 5 minutes, and a GitHub Actions health check runs a backup drain whenever the drain goes stale (AD-4, AD-18).

## Boundaries & Constraints

**Always:**
- **Enqueueing:** only through `core.jobs.enqueue(session, task, key, run_at=None, queue="default", **ids)`. It inserts a `job_outbox` row on the caller's session and never commits.
  - `ids` values must be `uuid.UUID` or `int`; anything else raises `ValueError`.
  - `key` must match `^<task_name>:[a-z_]+:[0-9a-f-]+(:[A-Za-z0-9_-]+)?$`, where `task_name` is the `<module>.<task>` name.
- **Defining tasks:** modules define tasks only through `core.jobs.task(name, queue=..., retry=...)`. It wraps the Procrastinate task so that each run records `job.succeeded` or `job.failed` through `core.ops.record_event()`, in its own transaction.
  - The default retry is exponential backoff with a maximum of 5 attempts.
  - import-linter forbids `app.modules` from importing `procrastinate`.
- **Flushing the outbox:** the drain moves outbox rows into Procrastinate with `queueing_lock=key`.
  - If the key is already enqueued, that counts as a dedup and the row is removed.
  - An outbox row is deleted only after its defer succeeds, so delivery is at-least-once.
- **The drain:** runs as a background task after the 202 response.
  - Order: take the advisory lock, flush the outbox, run `scheduler_tick`, retry stalled jobs (Procrastinate's stalled-jobs API, then retry), then run the worker with `wait=False` on `priority` and then `default`.
  - All of it runs under `asyncio.timeout(DRAIN_TIMEOUT_SECONDS)`; the default is 240 and the maximum is 240.
  - The drain holds a Postgres session advisory lock. An overlapping drain skips instead of running twice.
  - On completion it upserts `drain_state.last_drain_at`; the drain also records it as an `ops_events` row.
- **`scheduler_tick`:** due, unpaused `schedules` rows are enqueued and `next_run_at` is advanced, in one transaction. Never use `@app.periodic`.
- **Secrets on `/internal/*`:** every route needs `Authorization: Bearer <secret>`, compared with `hmac.compare_digest` against `DRAIN_SECRET` and, if set, `DRAIN_SECRET_PREVIOUS`. A bad or missing secret returns a 401 problem+json and runs nothing. `/internal/*` stays out of the OpenAPI schema.
- **`GET /internal/health`:** returns 200 `{"status":"ok","last_drain_at":...}`. It returns a 503 problem+json when `last_drain_at` is null or older than `DRAIN_STALE_AFTER_MINUTES` (15).
- **New tables** go in schema `app` with the RLS helper. Procrastinate's tables get it too, so the exposed-schema check stays green.
- **`ops_events` is append-only:** a trigger blocks UPDATE. Payloads are ids and integer counts only.

**Never:**
- Personal data or secrets in job arguments, outbox rows or `ops_events`. Secrets in the repo, including SQL.
- `task.defer()` or Procrastinate imports in `modules/`. Procrastinate on the transaction pooler.
- Consent checks, a real business task, or the router's priority-drain trigger. These belong to Stories 1.4, 1.6 and 1.5 respectively; the reusable `drain(queues=...)` function is in scope.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Enqueue then commit | `enqueue(...)` + commit | one outbox row; the next drain runs the task once | N/A |
| Enqueue then rollback | `enqueue(...)` + rollback | no outbox row, no job | N/A |
| Bad args | non-id kwarg or malformed key | nothing written | `ValueError` |
| Duplicate key pending | two enqueues, same key, before a run | one Procrastinate job | dedup |
| Drain, valid secret | `POST /internal/jobs/drain` with current or previous secret | 202; outbox flushed, due schedules enqueued, jobs run (`priority` first), `last_drain_at` set | task failures are retried per policy |
| Drain, bad secret | wrong or missing bearer token | 401 problem+json; nothing runs | N/A |
| Overlapping drain | drain while another holds the lock | 202; the second drain skips | logged |
| Health stale | `last_drain_at` null or older than 15 min | 503 problem+json | N/A |
| Health fresh | recent `last_drain_at` | 200 | N/A |
| Paused schedule | `paused=true`, due | not enqueued | N/A |

</frozen-after-approval>

## Code Map

Story 1.1 built the base this story extends. Reuse it; don't restructure it.
- `api/app/core/db.py` -- `make_engine` and the search_path hook. Procrastinate's `PsycopgConnector` pool must also `SET search_path TO app`, through a pool `configure` callback (no startup `options`).
- `api/app/core/config.py` -- add `drain_secret: SecretStr | None`, `drain_secret_previous: SecretStr | None`, `drain_timeout_seconds: int` (≤240) and `drain_stale_after_minutes: int = 15`.
- `api/app/core/errors.py` -- add `UnauthorizedError` (401, `unauthorized`) and `ServiceUnavailableError` (503, `drain-stale`).
- `api/app/main.py` -- `include_internal_router()` already exists. Mount `core/internal.py`'s router through it.
- `api/migrations/helpers.py` -- `create_app_table_rls()`.
- `api/scripts/check_exposed_schema.py` -- must still pass.
- `api/pyproject.toml` -- import-linter contracts; add `app.modules` ↛ `procrastinate`.
- `.github/workflows/` -- `ci.yml` and `migrate-deploy.yml` exist. Add `health-drain.yml`.
- `.github/workflows/ci.yml:25` -- add a trailing `# ggignore` comment, because GitGuardian flags the throwaway CI password.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/core/jobs.py` -- the Procrastinate `App` (connector with the search_path configure hook), `task()`, `enqueue()`, `flush_outbox()`, `scheduler_tick()`, `retry_stalled()` and `drain(queues=("priority","default"), timeout=...)`, plus the advisory lock -- AD-4.
- [x] `api/app/core/ops.py` -- `record_event(session, kind, *, owner_id=None, entity_type=None, entity_id=None, counts: dict[str,int] | None = None)` -- AD-18.
- [x] `api/app/core/internal.py` -- the secret dependency, `POST /jobs/drain` (202, run through `BackgroundTasks`) and `GET /health`.
- [x] `api/migrations/versions/0002_jobs.py` -- install Procrastinate's bundled schema SQL with `search_path=app`. Create these tables:
  - `job_outbox(id uuid pk, task, key, queue, run_at, args jsonb, created_at)`
  - `schedules(id, kind, owner_id uuid null, next_run_at, paused bool default false, interval)`, with a unique `(kind, owner_id)`
  - `ops_events(id, created_at, kind, owner_id, entity_type, entity_id, counts jsonb)`, with the UPDATE-blocking trigger
  - `drain_state(id smallint pk check (id=1), last_drain_at)`

  Apply RLS and revokes to every table in `app`.
- [x] `api/migrations/versions/0003_pg_cron_drain.py` -- if the `pg_cron` and `pg_net` extensions and the `vault` schema are available, enable them. Then `cron.schedule('careerlaunch-drain','*/5 * * * *', …)` calls `net.http_post` with the URL and bearer secret read from `vault.decrypted_secrets` (names `drain_url`, `drain_secret`) and `timeout_milliseconds := 90000`. Otherwise `RAISE NOTICE` and skip, so CI's plain Postgres still works. Downgrade unschedules the job.
- [x] `api/tests/test_jobs.py`, `test_internal.py`, `test_ops.py` -- every I/O matrix row. Include a real end-to-end run: enqueue a test task, commit, drain, and assert the task ran and the `job.succeeded` event exists.
- [x] `.github/workflows/health-drain.yml` -- `schedule: */15 * * * *` plus `workflow_dispatch`. Call `GET $API_BASE_URL/internal/health` with the bearer `DRAIN_SECRET`. On a non-200, `POST` the drain and fail the job; GitHub's failure email is the alert.
- [x] `.env.example`, `render.yaml`, `README.md` -- document the drain secrets (Render env and GitHub secrets), the Vault `drain_url` and `drain_secret` setup SQL as an operator step with placeholders only, and running a local drain with `scripts/drain_local.py`.

**Acceptance Criteria:**
- Given the migrated DB, when `check_exposed_schema.py` runs, then it passes with the Procrastinate, outbox, schedules, ops_events and drain_state tables present.
- Given the Supabase CLI stack with Vault secrets set, when 0003 runs, then `cron.job` has `careerlaunch-drain` with schedule `*/5 * * * *`, and its command uses `timeout_milliseconds := 90000` and reads its URL and secret from Vault.
- Given a module importing `procrastinate`, when `lint-imports` runs, then it fails.

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` with the roles SQL
- `cd api && uv run pytest tests/test_migrations.py tests/test_jobs.py` -- pass on the Supabase CLI Postgres (`npx supabase start`), where 0003 creates the cron job
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass
- `cd web && npm run gen:api` -- no diff (internal routes are not in OpenAPI)
- `actionlint` -- clean on all workflows

## Implementation Notes

- `enqueue(..., queue=None)`: the default falls back to the registered task's queue, so `task(queue="priority")` is honoured without every caller repeating it. Passing `queue="default"` explicitly behaves as specified.
- `task()` returns a `JobTask` (name, queue, func) rather than the Procrastinate task, so modules never get a `defer` handle. `retry` takes `core.jobs.RetryPolicy` (or `None`); `max_attempts=5` counts every run (maps to Procrastinate `max_attempts=4` retries), with exponential waits of 4, 16, 64 and 256 s.
- UUID args travel as strings in jsonb and are turned back into `uuid.UUID` before the task body runs (every str arg is a UUID because `enqueue` only admits UUID/int).
- The drain flushes the outbox a second time right after `scheduler_tick`, so due schedules run in the same drain rather than five minutes later. Order is otherwise as specified.
- `last_drain_at` is upserted only on a `completed` drain; every outcome records a `drain.<status>` ops event. The work runs under `timeout - 5 s` (min 1 s) so the worker's 5 s shutdown grace stays inside the 240 s cap; the aborted job goes back to `todo`.
- `enqueue` rejects unregistered task names and defaults `queue` to the registered task's queue. `scheduler_tick` pauses a schedule whose kind isn't a registered task and records `schedule.invalid`.
- `retry_stalled` counts the stalled run as an attempt: at the task's retry limit the job is marked failed and `job.failed` is recorded with `final=1`.
- `check_exposed_schema.py` also fails unless the connected role is superuser or BYPASSRLS (forced RLS with no policies).
- Job events: `entity_type` = task name, `owner_id` = the `owner_id` arg when present, `counts` = `{job_id, attempt}`. No FK on `ops_events.owner_id` / `schedules.owner_id` yet: `accounts` arrives in Story 1.3, which must add them (SET NULL / CASCADE).
- `schedules.kind` is the task name. Keys: `<kind>:owner:<owner_id>:<UTC slot>` or `<kind>:schedule:<schedule_id>:<UTC slot>`; missed slots are skipped.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap, plus orchestrator):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The procrastinate import contract also blocks the sanctioned `app.core.jobs.task` import (indirect) | high | Reproduced: adding `from app.core.jobs import task` to `profile/tasks.py` breaks the contract; the first real task (Story 1.6) would fail lint | patch |
| 2 | `health-drain.yml` fails every 15 min until secrets are set, so the owner gets an email each run after merge | high | The "Check secrets" step exits 1 when the secrets are empty; schedules run on main | patch |
| 3 | Health stays 200 when every drain times out | medium | `_mark_drained` upserts `last_drain_at` for `timed_out` too; the alert never fires | patch |
| 4 | Stalled or timed-out jobs re-queue forever, ignoring `max_attempts` | medium | `retry_stalled()` calls `retry_job` unconditionally; a job always over 240 s starves `default` | patch |
| 5 | Unregistered task names or `schedules.kind` are accepted; the job fails in the worker with no event | medium | `enqueue` checks only the name format; the `task()` wrapper never runs for an unknown task | patch |
| 6 | Invalid schedule rows are never advanced or paused, so `schedule_invalid` repeats on every drain | medium | `scheduler_tick` `continue`s without updating the row | patch |
| 7 | A scheduled task on the `priority` queue is deferred onto `default` | medium | A bare-string `enqueue` defaults to `DEFAULT_QUEUE` | patch |
| 8 | The worker grace period adds up to 5 s past the 240 s cap | low | `shutdown_graceful_timeout=5` after `asyncio.timeout(limit)`; direct fix | patch |
| 9 | Failed outbox rows are re-selected within one flush, inflating counts | low | The loop re-selects from the same ordering; a direct `id <> ALL(:failed)` exclusion | patch |
| 10 | `ServiceUnavailableError` is generic but hard-wired to the `drain-stale` slug | low | A future 503 would get the wrong type; direct rename to `DrainStaleError` | patch |
| 11 | Task idempotency (at-least-once) is undocumented | low | Neither the docstring nor the README states it; a one-line doc fix | patch |
| 12 | Jobs are only tested as a superuser, which bypasses forced RLS | medium | Gap filed pre-verified; Supabase `postgres` has `rolbypassrls`, but nothing asserts it | patch |
| 13 | The drain-failure containment test passes without the try/except | medium | Gap filed pre-verified | patch |
| 14 | The invalid-schedule branch is untested | medium | Gap filed pre-verified | patch |
| 15 | The outbox multi-batch loop is untested | medium | Gap filed pre-verified | patch |
| 16 | No test reads the configured stale-after or timeout settings | medium | Gap filed pre-verified | patch |
| 17 | The pg_cron migration never runs in CI | medium | Gap filed pre-verified; the local Supabase stack run passed; CI needs a pg_cron-capable image | defer |
| 18 | No retention for finished `procrastinate_jobs`, `procrastinate_events` or `ops_events` | medium | `DeleteJobCondition.NEVER`; retention is AD-17's job and has no story here | defer |
| 19 | Per-drain DB connections vs the Supavisor free-tier limit | maybe-false | Needs the hosted project's pooler client limit; worst case the lock conn + pool 4 + SQLAlchemy pool | defer (medium, unverified) |
| 20 | A non-timeout drain exception writes no ops event | low | Health goes stale and the alert covers it; `drain_failed` is logged | reject |
| 21 | CancelledError leaves no `job.failed` event | low | Documented; a fix adds shielded recording complexity | reject |
| 22 | A non-UUID string arg raises on decode | low | Only `enqueue` writes args and it validates them | reject |
| 23 | The connector URL is fixed at import | low | Settings are fixed per process in deployment | reject |
| 24 | pg_cron not in `shared_preload_libraries` | low | Supabase preloads pg_cron | reject |
| 25 | `drain_local --every 0` makes a tight loop | low | Local dev tool; a guard adds a branch | reject |
| 26 | The 401 lacks `WWW-Authenticate` | low | Machine-to-machine endpoint; no client depends on it | reject |
| 27 | Drain counts lack worker run, success and failure totals; retries indistinguishable from final failure | low | Diagnostic nicety; needs new plumbing | reject |
| 28 | health-drain curl retries a 503; 60-day schedule disable; environment rules | low | Duplicate drains are lock-safe; no protection rules are configured | reject |
| 29 | README hardcodes "15 minutes" | low | Matches the default | reject |
| 30 | Weak tests: hand-patched `asyncio.timeout`, the concurrent-drain test accepts both outcomes | low | Style; lock behavior is covered by `test_overlapping_drain_skips` | reject |
| 31 | `drain(timeout<=0)` | low | No caller passes it | reject |
