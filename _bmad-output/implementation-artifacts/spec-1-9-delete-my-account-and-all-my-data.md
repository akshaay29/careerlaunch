---
title: 'Story 1.9: Delete my account and all my data'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '17c3793ffec3abeea9e32f3cbd3616785f8fc9c3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Student cannot leave. The DPDP Act and FR-2 require that the account and everything CareerLaunch holds about the Student can be deleted completely: database rows, stored files and the Supabase Auth user, well within 30 days. Global logs must keep no personal data (AD-17, NFR11).

**Approach:**
- `DELETE /api/v1/me`, confirmed by typing the Student's email, runs one transaction that:
  - publishes `AccountDeleting`;
  - records a tombstone;
  - deletes the `accounts` row, which cascades every Student-owned row;
  - enqueues idempotent `purge_files` and `delete_auth_user` jobs.
- The jobs remove `<owner_id>/` from Storage and the Auth user through new port capabilities.
- A tombstone stops a still-valid token from re-provisioning the account.
- `web` adds a confirm-by-email deletion page and signs the user out.

## Boundaries & Constraints

**Always:**
- **Ports:**
  - `FileStore.delete_prefix(prefix) -> int` lists every object under the prefix (recursing into folders), deletes them in batches, and returns the count. It is idempotent: an empty prefix returns 0.
  - New `AuthAdmin` port (`app/ports/auth_admin.py`) with `delete_user(user_id)`.
    - Adapter `app/adapters/supabase_auth/`: `DELETE <SUPABASE_URL>/auth/v1/admin/users/{id}` with the service-role key, through `httpx`.
    - A 404 counts as already deleted (success).
    - Wired like the `FileStore`, with an in-memory fake that can be told to fail.
- **Tombstone:** a global table `app.deleted_accounts(id uuid PK, deleted_at)`.
  - No FK, no other columns: the Auth uid is a pseudonymous id and no email is kept. RLS via the helper.
  - `accounts.service.get_or_provision` raises 401 for a tombstoned id, so a token issued before deletion can't recreate the account.
- **Endpoint** `DELETE /api/v1/me`, body `{confirm_email}`:
  - The confirmation must equal the account email after trimming and ignoring case. Otherwise 422 on `confirm_email`, and nothing changes.
  - Then, in one transaction:
    1. publish `AccountDeleting(owner_id)` (added to `core.events`);
    2. insert the tombstone;
    3. `record_event(kind="account.deleted", owner_id=None, counts={})`;
    4. enqueue `accounts.purge_files` and `accounts.delete_auth_user` (key `<task>:account:<id>`, ids `owner_id`), on the `priority` queue, each with a long retry policy that spans days;
    5. delete the `accounts` row.
  - Commit, return 204, and start the after-response priority drain.
  - Admins may delete themselves the same way.
- **Jobs** (in `accounts.tasks`, ids only):
  - `purge_files` calls `FileStore.delete_prefix(f"{owner_id}/")` and records `ops_events` kind `account.files_purged` with `counts={"objects": n}`.
  - `delete_auth_user` calls `AuthAdmin.delete_user(owner_id)` and records `account.auth_deleted`.
  - Neither event stores an owner or entity id.
  - Both are idempotent, so re-runs and retries are safe.
- **`core.ops.record_event` tolerates a deleted owner:** an `owner_id` whose account no longer exists is stored as NULL instead of failing the insert. This resolves the Story 1.2 deferral, and job bookkeeping for deleted owners is no longer lost.
- **Global rows:** `ops_events` and `admin_audit` keep no personal data (ids and counts only), and their Student FKs go to NULL (already in place since Story 1.3).
- **Convention check:** a test inspects the catalog and asserts:
  - every `app` table with an `owner_id` column has an FK to `accounts(id)` with `ON DELETE CASCADE`;
  - the only exceptions are an explicit allow-list, `ops_events` (SET NULL);
  - every other FK to `accounts` is CASCADE or SET NULL.
  
  New tables are then checked automatically, not only in code review.
- **Web:**
  - `/settings/account` has a "Delete my account" danger section. It explains what is deleted and that the deletion can't be undone, and has an input where the Student types their email. The button stays disabled until the typed email matches (ignoring case and surrounding spaces); the server checks again.
  - On 204: Supabase `signOut()`, then redirect to a public `/account-deleted` page.
  - The `/settings` hub links to it. 422 errors are shown on the field.

**Never:**
- Soft-delete, or keeping the email or any PII anywhere after deletion.
- A grace period or undo.
- `revoke_google`: no Google connection exists yet, so it arrives with Gmail (a later epic).
- Admin-initiated deletion of other users.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Delete | Student with consents, resumes, documents, skill profile, preferences and schedules; correct email | 204; `accounts` row gone; every owned row gone; tombstone present; `AccountDeleting` once before commit; two jobs enqueued | N/A |
| Email mismatch | wrong `confirm_email` | 422 on `confirm_email`; nothing changed | field-level problem |
| Case and space | `"  STUDENT@X.COM "` | accepted | N/A |
| Purge files | objects under `<owner>/resume/` and `<owner>/document/` (fake store) | all removed; other owners' objects untouched; `account.files_purged` with an object count and no owner id | N/A |
| Purge re-run | job runs twice | second run removes 0 objects and succeeds | N/A |
| Storage down | `delete_prefix` raises | job raises, so it is retried; nothing marked done | N/A |
| Auth delete | fake Auth admin | user deleted; `account.auth_deleted` recorded | N/A |
| Auth already gone | 404 from Supabase (adapter test) | treated as success | N/A |
| Stale token | valid token for a deleted account | 401; no `accounts` row recreated | problem+json |
| Global rows | `ops_events` and `admin_audit` referencing the Student | FKs NULL; rows remain | N/A |
| `record_event` for a deleted owner | owner id with no account | row inserted with `owner_id` NULL | N/A |
| Convention | catalog scan | every `owner_id` table cascades from `accounts` | test failure names the table |
| No token | `DELETE /me` | 401 | problem+json |
| Other Students | a second Student's data | untouched | N/A |

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.8. Reuse them; don't restructure.
- `api/app/core/events.py` -- add `AccountDeleting(owner_id)`.
- `api/app/core/ops.py` -- `record_event`; make an `owner_id` that has no account become NULL, e.g. `INSERT … SELECT … (SELECT id FROM accounts WHERE id = :owner)`. `record_audit` stays as it is.
- `api/app/core/jobs.py` -- `task(name, queue, retry)`, `enqueue`, `RetryPolicy`. `profile/tasks.py` shows a long-retry task. Check how the job framework records per-job events with an owner (the source of the deferral).
- `api/app/core/auth.py` -- `current_actor` calls the installed account loader; the tombstone check belongs in `accounts.service.get_or_provision`, returning `UnauthorizedError`.
- `api/app/modules/accounts/{service,repo,models,router,schemas,tasks}.py` -- `/me` lives here. Add the deletion service, the tombstone model and repo, and the two tasks. `accounts` sits at the bottom of the module order, so it can't import `profile`: purge by prefix only.
- `api/app/ports/file_store.py`, `api/app/adapters/supabase_storage/store.py`, `api/tests/fakes/file_store.py` -- add `delete_prefix`. Supabase list API: `POST /storage/v1/object/list/{bucket}` with `{prefix, limit, offset}`. Entries with `id: null` are folders. Delete through the existing batch `delete`.
- `api/app/main.py` -- `FileStore` install pattern (`create_app(file_store=...)`, `LazyFileStore`); add `auth_admin=` the same way. `scripts/drain_local.py` installs ports too.
- `api/app/modules/profile/router.py` `drain_priority` -- reuse it for the after-response drain. Don't import `profile` from `accounts`: move the helper to `core`, e.g. `core.jobs.drain_priority_after_response`, and have both call it.
- `api/migrations/versions/0009_documents.py` -- latest; the new one is `0010_deleted_accounts.py`. Bump `HEAD`.
- `web/src/app/settings/page.tsx` (hub), `web/src/components/auth/sign-out-button.tsx` (the `signOut` pattern), `web/src/lib/auth-paths.ts` (`/account-deleted` must be public, not protected).

## Tasks & Acceptance

**Execution:**
- [x] Ports and adapters: `FileStore.delete_prefix` (port, Supabase adapter, lazy wrapper, fake), and the `AuthAdmin` port, Supabase adapter and fake, wired in `app.main` and `drain_local`. Adapter tests use `httpx.MockTransport`.
- [x] `api/app/core/{events,ops,jobs}.py` -- `AccountDeleting`, a `record_event` that tolerates a deleted owner, and the shared after-response drain helper.
- [x] `api/migrations/versions/0010_deleted_accounts.py` -- the tombstone table and RLS.
- [x] `api/app/modules/accounts/*` -- `delete_account` (service), `DELETE /me` (router), tombstone handling in `get_or_provision`, and the `purge_files`/`delete_auth_user` tasks.
- [x] `api/tests/test_account_deletion.py`, `test_owner_fk_convention.py`, adapter tests -- every matrix row.
- [x] `web/**` -- regenerate the client; add `/settings/account` with the delete form, the public `/account-deleted` page, the hub link and helper tests.
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` is NOT edited -- README: what account deletion removes and the 30-day guarantee.

**Acceptance Criteria:**
- Given a deleted Student, when the priority drain has run, then the fake store has no `<owner>/` objects, the fake Auth admin has no user, and `ops_events` has both purge rows with no owner or entity ids.
- Given the full test suite, when it runs, then all previous stories' tests still pass.

## Design Notes

- **Why a tombstone?** A Supabase access token stays valid for up to an hour after the Auth user is deleted, and `current_actor` provisions accounts on first sight. Without the tombstone, one request in that window would silently recreate an empty account. The tombstone keeps only the uuid, which Supabase never reuses.
- **Why the priority queue?** It lets the after-response drain finish the purge within seconds. The long retry policy and the 5-minute cron cover outages, keeping completion well within 30 days.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap). Manual check run by the orchestrator against the real local Supabase stack: `delete_prefix` removed 3 objects across `resume/` and `document/` and left another owner's object; a re-run removed 0; `delete_user` succeeded, a second call tolerated the 404, and the user lookup returns 404.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The 30-day guarantee has no backstop: `PURGE_RETRY` gives up after ~4 days, nothing re-runs a failed purge, and nothing records whether a given deletion finished | high | `accounts/tasks.py` 10 attempts with 4^n waits; the README and UI say "always within 30 days" | patch |
| 2 | Lazy port wrappers (`LazyFileStore.delete_prefix`, `LazyAuthAdmin.delete_user`), which production uses, are never exercised | medium | Gap filed pre-verified; deletion tests inject fakes directly | patch |
| 3 | The tombstone check-then-upsert in `get_or_provision` isn't atomic; a deletion committing in between lets the upsert recreate the account | low | `is_deleted()` then `upsert_email()`; direct fix: make the insert conditional on no tombstone | patch |
| 4 | `record_event` silently turns any unknown `owner_id` into NULL, hiding caller bugs | low | New `INSERT … SELECT`; direct fix: log a warning when a non-NULL owner is dropped | patch |
| 5 | Two concurrent `DELETE /me` requests: the second gets 404 and the UI says "Could not delete" | low | Missing row after the lock gives `NotFoundError`; direct fix: 204 when the tombstone exists | patch |
| 6 | Fake and real `delete_prefix` disagree on prefix rules | low | The fake requires a trailing `/`; the adapter rstrips and rejects `..` | patch |
| 7 | The Auth admin base URL is derived from the token issuer, not `SUPABASE_URL` | low | `from_settings` uses `supabase_issuer`; direct fix | patch |
| 8 | Wiring test leaves process-wide ports installed; storage-down test uses `pytest.raises(Exception)` | low | Test hygiene; direct fixes | patch |
| 9 | No recent-authentication check or rate limit on the irreversible `DELETE /me`; the confirmation email is shown on the page | medium | Needs a reauth step (Supabase `reauthenticate`/OTP) and a product decision | defer |
| 10 | No component tests for `DeleteAccount` | medium | No `web` harness, as in Stories 1.4–1.8 | defer |
| 11 | Jobs queued for the owner before deletion run afterwards against missing rows | false | `extract_resume` no-ops when the Resume is gone; `delete_*_file` and `purge_files` are idempotent deletes | reject |
| 12 | An in-flight upload can orphan an object after the purge | false | The upload path's `BaseException` cleanup deletes the stored object when the insert fails (FK violation) | reject |
| 13 | `procrastinate_jobs.args` and queueing locks keep the owner uuid | low | Covered by the existing AD-17 deferral (retention of finished jobs); a pseudonymous id, not PII | reject |
| 14 | The tombstone keeps uids forever | low | Pseudonymous id, needed to block stale tokens; documented in the spec Design Notes | reject |
| 15 | A last admin can delete themselves | low | Admins are created only through the `promote-admin` CLI; the owner can re-promote | reject |
| 16 | A large purge can exceed the 25 s drain budget | low | Deletions are batched and persist, so retries make progress; the sweep from #1 finishes it | reject |
| 17 | An in-flight `record_event` during deletion can still hit the FK | low | A narrow race; the job wrapper logs it; bookkeeping only | reject |
| 18 | Web: `signOut` errors are swallowed; a second tab gets a 401 message | low | Edge UX; the account is deleted either way | reject |
| 19 | A tombstoned user signing in before the Auth delete sees 401 everywhere | low | The Auth delete runs seconds later in the after-response drain | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack: sign up, upload a Resume and a Document, then delete the account at `/settings/account`. Check that the bucket has no `<owner>/` objects, that the Auth user is gone in Studio, and that signing in again fails.
