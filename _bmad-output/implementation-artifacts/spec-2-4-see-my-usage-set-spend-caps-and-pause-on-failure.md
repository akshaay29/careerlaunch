---
title: 'Story 2.4: See my usage, set spend caps, and pause on failure'
type: 'feature'
created: '2026-11-15'
status: 'done'
baseline_commit: '021e51fe99de735137d84d2fbd1bb864c74d829c'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-3-collect-linkedin-listings-on-a-schedule.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Collection spends the Student's Apify credit, but the Student can't see what runs cost, can't cap spend, and a revoked key or exhausted credit keeps producing failed runs every day (FR-28, FR-6, AD-12).

**Approach:**
- Add Student-set per-run and monthly caps, plus an optional platform-wide monthly cap from env, all checked in `start_provider_run` before any spend.
- Apify auth and credit failures pause the Student's `provider_status` with a reason.
- Show status, caps, monthly spend and recent runs on the Provider Key settings page.

## Boundaries & Constraints

**Always:**
- **Caps** live in a new `spend_caps` table owned by `accounts`.
  - Columns: `owner_id` PK → accounts CASCADE, `per_run_usd`, `monthly_usd`, `updated_at`, with RLS.
  - A missing row means the defaults: per-run = `COLLECT_MAX_CHARGE_USD` (0.25), monthly = new `DEFAULT_MONTHLY_CAP_USD` (4.00, under Apify's USD 5 free credit).
  - Bounds: per-run 0.05–2.00; monthly 0.25–100, and monthly ≥ per-run. Anything outside is a 422.
  - Endpoints: `GET /api/v1/spend-caps` and `PUT /api/v1/spend-caps`.
- **Estimate.** New config `APIFY_PRICE_PER_ITEM_USD` (default 0.001).
  - `estimated_cost_usd = max_items × price`, stored on every `provider_runs` row (new column).
  - The run's `max_charge_usd` is the Student's per-run cap.
- **Cap checks** in `_refusal`, after the existing checks, under a per-owner advisory lock taken in addition to the `run_key` lock. Each refusal is a `refused` row with this `error_kind`:
  - `run_cap`: the estimate is above the per-run cap.
  - `monthly_cap`: the Student's month-to-date committed spend plus this run's per-run cap is above the monthly cap.
    - Month to date is the current UTC calendar month, by `created_at`.
    - Committed spend is `cost_usd` for finished runs, plus `max_charge_usd − cost so far` for `starting`/`running` runs.
  - `platform_cap`: the same calculation over all Students is above the optional `PLATFORM_MONTHLY_CAP_USD` (unset means off).
  - The System seed run skips Student caps.
- **Pause on failure** through new `accounts.service.pause_provider(session, owner_id, reason, exhausted_until=None)`, called in `_fail_run`'s transaction for Student runs, from both start and poll:
  - `ProviderKeyInvalid` → reason `key_invalid`.
  - `ProviderRejected` gains a `kind`, set by the adapter from Apify's error type and HTTP status:
    - `credit` (402, or a usage/credit-limit error type) → reason `credit_exhausted`, `exhausted_until` = the billing-cycle end.
    - `permission` (403 `insufficient-permissions`) → reason `not_permitted`.
    - `blocked` (any other 403) → reason `account_blocked`.
  - **Billing-cycle end:**
    - Comes from new `ProviderRunner.billing_cycle_end(token) -> datetime | None`. It calls `GET /v2/users/me/limits` and reads `monthlyUsageCycle.endAt`; confirm the field in Apify's API docs.
    - It never raises.
    - It is called with no transaction open.
    - Fallback: the first instant of the next UTC month.
  - Seed runs never pause.
- **Resume, with no schedule changes:**
  - Pauses don't touch `schedules`. The daily tick keeps enqueuing; while paused, `start_provider_run` refuses cheaply with `paused`/`exhausted`, so no Apify call is made.
  - `credit_exhausted` stops blocking once `exhausted_until` has passed (`blocks_runs`), so collection resumes on the next tick.
  - `store_credential` (a fixed key) clears `provider_status` in the same transaction, so the next tick runs.
  - This settles the Story 2.2/2.3 deferral: resaving a key never overrides cap refusals, because caps are rechecked at every start.
- **Usage API:** `GET /api/v1/provider-usage` in the `listings` router returns:
  - `status` (`active`/`paused`, `reason`, `exhausted_until`);
  - `caps`;
  - `month` (`spent_usd`, `committed_usd`, `monthly_cap_usd`);
  - the owner's latest 30 runs: `created_at`, `status`, `error_kind`, `estimated_cost_usd`, `cost_usd`, `items`, `ingested`.
  - Never params, run ids or tokens. Regenerate the web client.
- **Web** (`/settings/provider-key`), below the key manager:
  - a paused banner with human copy per reason, and the resume date for credit;
  - a cap form;
  - a month spend line;
  - a runs table with a plain-language outcome per `status`/`error_kind`.
  - Pure helpers go in `web/src/lib/provider-usage.ts` with `node --test` tests.
- One migration, `0014_spend_caps`: `spend_caps`, `provider_runs.estimated_cost_usd`, and a `provider_status.reason` check over the four reasons.

**Never:**
- Pausing or unpausing `schedules` rows for caps or provider status.
- Calling Apify inside a DB transaction.
- Exposing tokens, raw Apify error bodies or run ids to the client.
- Expiry and eviction (2.5).
- Aborting runs on account deletion (still deferred).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Defaults | no `spend_caps` row | run starts with `max_charge_usd`=0.25 and an estimate of 0.10 (100 items) | N/A |
| Per-run cap | per-run 0.05, 100 items | `refused` `run_cap`; runner not called | N/A |
| Monthly cap | spent 3.90, monthly 4.00, per-run 0.25 | `refused` `monthly_cap` | N/A |
| In-flight counts | one `running` row with cap 0.25 and cost so far 0.05, spent 3.60 | committed 3.80; a new run → `monthly_cap` | N/A |
| Platform cap | env 1.00, other Students committed 0.90 | `refused` `platform_cap` | N/A |
| Invalid key on start | runner raises `ProviderKeyInvalid` | run `failed key_invalid`; status `paused`/`key_invalid` | N/A |
| Credit exhausted on poll | `ProviderRejected(kind=credit)` | `paused`/`credit_exhausted`, `exhausted_until` = cycle end, or next UTC month if unknown | limits call fails → fallback |
| Paused | status paused, tick | `refused` `paused`; no Apify call | N/A |
| Cycle reset | `exhausted_until` in the past | next collect starts normally | N/A |
| Key fixed | `PUT /provider-key` succeeds | `provider_status` cleared; next run starts | N/A |
| Caps API | per-run 3.00, or monthly < per-run | 422 | N/A |
| Usage API | another Student's runs exist | only own runs; no params, run ids or tokens | 401 signed out |

</frozen-after-approval>

## Code Map

- `api/app/modules/listings/service.py` -- `_refusal` (add caps), `start_provider_run` (estimate, per-owner lock, per-run cap as `max_charge_usd`, pause on start errors), `poll_provider_run` (pause on `key_invalid`/`rejected`), `_fail_run` (accept an optional pause and write it in its transaction). Error-kind strings are already listed in the 2.3 spec Implementation Notes.
- `api/app/modules/listings/repo.py` -- add month-to-date committed-spend queries (per owner and global) and the latest-runs query.
- `api/app/modules/listings/router.py`, `schemas.py` -- `GET /provider-usage`.
- `api/app/modules/accounts/service.py` -- `ProviderStatusView.blocks_runs` (credit auto-resume), `get_provider_status`, `store_credential` (clear status), new `pause_provider`, `get_spend_caps`, `set_spend_caps`. `repo.py`, `models.py`, `router.py`, `schemas.py` -- the `spend_caps` model and repo, the caps endpoints.
- `api/app/ports/provider_runner.py`, `api/app/adapters/apify/runner.py` (`_run_call` error mapping, around lines 181–206; `_error_type`), `api/tests/fakes/provider_runner.py` -- `ProviderRejected.kind`, `billing_cycle_end`.
- `api/app/core/config.py` -- `default_monthly_cap_usd`, `apify_price_per_item_usd`, `platform_monthly_cap_usd: float | None`. Update `.env.example` and `render.yaml`.
- `api/migrations/versions/0014_spend_caps.py` -- after `0013_provider_runs`. Bump the HEAD test. The owner-FK convention test must pass.
- `web/src/app/settings/provider-key/page.tsx`, `web/src/components/provider-key/` (new `provider-usage.tsx`), `web/src/lib/provider-usage.ts` (+ `.test.ts`). Follow `provider-key.ts` and its tests for patterns.
- Fixtures: low-entropy fake ids and tokens only (`fakerun0001`, `apify_api_FAKE…` with `# ggignore`). GitGuardian scans every commit.

## Tasks & Acceptance

**Execution:**
- [x] Config and migration 0014; `accounts` caps, pause and clear (service, repo, router, schemas).
- [x] Port, adapter and fake: `ProviderRejected.kind` and `billing_cycle_end` (with adapter MockTransport tests).
- [x] `listings` caps, estimate and pause wiring; the `/provider-usage` endpoint; `npm run gen:api`.
- [x] Web usage section and helpers, with tests.
- [x] Tests: `api/tests/test_spend_caps.py`, `test_provider_pause.py`, and usage API tests covering every matrix row. README: caps and pause behaviour.

**Acceptance Criteria:**
- Given the fake runner, when a key is saved and then the runner reports exhausted credit on a poll, then the Student's usage shows `paused`/`credit_exhausted` with a date, later collects are refused without runner calls, and after the date passes the next collect starts a run.
- Given the full API and web suites plus lint, typecheck and import-linter, when they run, then everything passes.

## Design Notes

- **Refuse instead of pausing schedules.** A refused row costs nothing and leaves a visible daily record ("paused: credit exhausted"). Resuming then needs no event plumbing; the deferral about schedule pauses fighting each other disappears.
- **Worst-case monthly accounting.** In-flight runs count at their full remaining cap, so two quick runs can never together exceed the monthly cap.

## Implementation Notes

- **Apify limits endpoint confirmed (2026-11-15, docs.apify.com `users-me-limits-get`):** `GET /v2/users/me/limits` returns `data.monthlyUsageCycle.endAt` (required, ISO 8601 date-time in UTC, e.g. `2022-11-01T23:59:59.999Z`). `billing_cycle_end` parses it; any failure (status, body, transport, parse) is logged by exception type and gives None.
- **Rejection kinds:** `ProviderRejected(message, *, kind="blocked")`. The adapter (`rejection_kind`) maps 402, or any error type matching `usage|credit|payment` (lower-cased; a bare `limit-exceeded` is not credit) (e.g. `not-enough-usage-to-run-paid-actor`), to `credit`; 403 `insufficient-permissions` to `permission`; other 403s to `blocked`. Token validation (Story 2.2) is unchanged.
- **Run `error_kind` unchanged:** a refused run is still `failed`/`rejected` (or `key_invalid`); the pause reason lives in `provider_status.reason`. New refusal kinds: `run_cap`, `monthly_cap`, `platform_cap`.
- **Spend accounting** (`listings.repo.month_spend`): `spent` = sum of `cost_usd` (cost so far for runs in flight) over non-refused rows created this UTC month; `committed` = `spent` + `max(max_charge_usd - cost so far, 0)` for `starting`/`running` rows. Matrix row "In-flight counts": 3.55 finished + running (cap 0.25, cost 0.05) = spent 3.60, committed 3.80.
- **Locks:** `run_key` lock, then `spend:owner:<id>`, then (only when `PLATFORM_MONTHLY_CAP_USD` is set) a global `spend:platform` lock, always in that order. The platform lock serialises Student starts while a platform cap is configured; without it two Students could pass the platform cap together.
- **Seed and platform cap:** the seed run (no owner) skips every cap, including the platform cap, and seed rows are excluded from the platform total ("over all Students"). The seed still sets its own `max_charge_usd` and now records an estimate.
- **Student runs can't set `max_charge_usd`:** `start_provider_run` raises `ValueError` if an owner run passes it; the charge is always the Student's per-run cap.
- **Pause write:** `_fail_run(..., pause=Pause(reason, exhausted_until))` writes `pause_provider` in the same transaction as the failure, and still writes (and commits) it when the row had already moved on (e.g. reaped), since Apify's answer holds either way. `_pause_for` reads the cycle end before `_fail_run` opens its transaction. A cycle end that is unknown or not in the future falls back to the first instant of the next UTC month.
- **Status semantics:** a credit pause is stored as `paused`/`credit_exhausted`/`exhausted_until`. `ProviderStatusView.blocks_runs` returns `exhausted` until then and None afterwards; `effective(now)` (used by the usage API) shows a lapsed pause as active, and a legacy `active` row with a future `exhausted_until` as paused/credit_exhausted.
- **`store_credential`** deletes the `provider_status` row (missing = active) in its transaction.
- **Caps API:** `SpendCapsInput` uses `Decimal` with the bounds in the schema (422 from validation); `set_spend_caps` re-checks bounds and `monthly >= per_run` (422 `InvalidInputError`), under the account row lock. The DB has matching CHECK constraints.
- **`estimated_cost_usd`** is nullable (rows from before 0014 have none); every new row gets it.
- **Lint/type tooling:** the repo has no ruff/mypy config or dependency (CI runs pytest, import-linter and the exposed-schema check). Checked with `uvx ruff` and `uv run --with mypy`: no new mypy errors versus baseline (25 pre-existing), new app code has only the same `BLE001` pattern already used in the module.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Spend accounting counts NULL-cost failed runs (`start_unknown`, `timeout`, reaped) as 0 and ignores an in-flight run created before the month start, so a cap can be exceeded | medium | `repo.month_spend` sums `coalesce(cost_usd, 0)` and filters `created_at >= since` for every status | patch |
| 2 | A stale rejection (old token, in-flight poll) writes a pause after `store_credential` cleared it, pausing a valid new key | medium | `_fail_run` writes the pause with no check that the key is still the one that failed | patch |
| 3 | `_CREDIT_TYPE_RE` matches a bare `limit-exceeded` and is case-sensitive, so a non-credit limit could pause a Student until the cycle end | medium | `rejection_kind` classifies any matching 402/403 as `credit` | patch |
| 4 | Config values are not cross-validated: default estimate above `COLLECT_MAX_CHARGE_USD`, default per-run outside 0.05-2.00, or default monthly below per-run, refuses every run or fails the prefilled caps form | medium | `config.py` bounds are independent (`collect_max_charge_usd` le 50, `default_monthly_cap_usd` ge 0.25) | patch |
| 5 | The usage section stays stale after the key is replaced or deleted (banner says "replace it above" but never refreshes) | medium | `ProviderUsageSection` and `ProviderKeyManager` are siblings with no refetch | patch |
| 6 | `onSaveCaps` resets `busy`/`pending` outside `finally`; a failed refetch after a successful save replaces the section with an error | low | `provider-usage.tsx` `onSaveCaps` | patch |
| 7 | `formatUsd` rounds to 2 decimals, hiding sub-cent run costs; "this month" is the UTC month, not Apify's cycle; banner says "after <date>" when the fallback resumes at 00:00 UTC of that date | low | `provider-usage.ts` helpers and copy | patch (copy and precision only) |
| 8 | `formatDateTime` throws on an invalid string | low | `Intl.DateTimeFormat.format(new Date(bad))` | patch (guard) |
| 9 | No tests: concurrent starts under the per-owner and platform locks; blank `PLATFORM_MONTHLY_CAP_USD`; a pause arriving after the run was reaped; a stale pause after a key fix; NULL-cost accounting; month-aggregate assertions skipped on the 1st | medium | Verification-gap review; read the three new test files | patch |
| 10 | Import order in `adapters/apify/runner.py` | low | `RunState, RejectionKind, RunStatus` | patch |
| 11 | A per-run cap below the default estimate (USD 0.10) guarantees every run is `run_cap`-refused | low | The spec's matrix row "Per-run cap" accepts this behaviour; the reason is visible to the Student | defer (a form hint) |
| 12 | Re-saving the same key clears a `credit_exhausted` pause | false | A re-saved key may belong to another Apify account; at worst one cheap refused call re-pauses it | reject |
| 13 | A `blocked` or `not_permitted` pause has no periodic re-probe | low | Only a key re-save clears it; matches the spec | defer |
| 14 | Daily `refused` rows can crowd out real runs in the latest 30; no index for the platform-wide month scan | low | `latest_runs` has the 0013 owner/created index; the platform cap is off by default | defer |
| 15 | Unknown `provider_runs.status` would 500 the usage endpoint | false | The table has a CHECK over the five statuses | reject |
| 16 | `pause_provider` and `set_spend_caps` emit no ops event | low | Each failed run already records `listings.provider_run_failed` with its kind | defer |
| 17 | Caps form accessibility (`aria-invalid`, focus) and bounds duplicated in four places | low | `precheckCaps` returns a field the component ignores | defer |
| 18 | The spec requires ruff and mypy, which the repo does not have | false | Pre-existing: CI runs pytest, import-linter and the schema check only; the fix would edit this spec | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres container)
- `cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run lint-imports` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean
