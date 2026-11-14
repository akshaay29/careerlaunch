---
title: 'Story 1.5: Upload and manage Resume versions'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: 'af61f1104cd5ab19dc49c02306e5dfb19b3ded52'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Students cannot give CareerLaunch a Resume. Skill extraction (Story 1.6), scoring (Epic 3) and Applications (Epic 4) all need a stored Resume, with one active version the Student chose, kept private to its owner (FR-3, FR-26).

**Approach:**
- `profile` accepts a PDF or DOCX upload of up to 5 MB, after checking its magic bytes and size.
- The file is stored through a new `FileStore` port, implemented by a Supabase Storage adapter, in a private bucket at `<owner_id>/resume/<resume_id>`. Each upload gets a `resumes` row.
- Students list, activate, download (short-lived signed URL) and delete their versions.
- Activating publishes `ResumeActivated` in the same transaction.

## Boundaries & Constraints

**Always:**
- **`FileStore` port** (`app/ports/file_store.py`, imports only `core`): `put(path, data, content_type)`, `delete(paths)` (idempotent: a missing object is not an error), and `signed_download_url(path, expires_in_seconds, download_name) -> str`.
- **The adapter** lives in `app/adapters/supabase_storage/`:
  - It talks to the Supabase Storage REST API with `httpx` (already pinned) and the service-role key. No vendor SDK.
  - A signed URL must make the download an attachment (Storage's `download=<name>` parameter).
- **Fake:** an in-memory fake goes in `api/tests/fakes/`. Tests never touch real Storage.
- **Wiring:** the store is wired in `app.main` (like the account loader) and reaches the service as a dependency, so `modules` never import `adapters`.
- **Config:**
  - `SUPABASE_SERVICE_ROLE_KEY` (SecretStr, required when the real adapter is used).
  - `STORAGE_BUCKET` (default `private`).
  - `RESUME_MAX_VERSIONS` (default 10).
  - Add all three to `.env.example` and `render.yaml`.
- **Bucket:**
  - Local: `supabase/config.toml` declares the bucket as private, with a 5 MiB file size limit.
  - Hosted: `api/scripts/ensure_bucket.py` (idempotent: create if missing, never make it public) runs in `migrate-deploy.yml` after migrations.
- **Upload** (`POST /api/v1/resumes`, multipart field `file`):
  - Read at most 5 MB + 1 byte. More than 5 MB gives 413 problem+json, "Resume must be 5 MB or smaller". An empty file gives 415.
  - Type comes from content, never from the filename or the client's content type:
    - PDF starts with `%PDF-`.
    - DOCX is a ZIP that contains `word/document.xml`.
    - Anything else gives 415, "Upload a PDF or DOCX file".
  - Reaching `RESUME_MAX_VERSIONS` gives 409, "Delete an old version first". Nothing is stored in any of these cases.
  - Order of operations:
    1. Store the file.
    2. Insert the row.
    3. If the transaction fails, delete the file (best effort) before re-raising.
- **Rows:** `app.resumes(id uuid v7 = storage uuid, owner_id → accounts ON DELETE CASCADE, filename, content_type, size_bytes, sha256, is_active bool, created_at)`.
  - The partial unique index allows one `is_active` row per owner.
  - RLS via the helper.
  - `filename` is the sanitised original name (path stripped, control characters removed, at most 255 characters), used only for display and the download name.
- **Activation** (`POST /api/v1/resumes/{id}/activate`): deactivate the current active row, activate this one, and publish `ResumeActivated(owner_id, resume_id)` (added to `core.events`), all in one transaction. Already active: 200, no event.
- **List, download and delete:**
  - `GET /api/v1/resumes` returns the owner's versions, newest first, using the cursor-pagination convention (`limit` default 20, max 50; `next_cursor`).
  - `GET /api/v1/resumes/{id}/download` returns `{url, expires_at}`, with an expiry of 300 s or less.
  - `DELETE /api/v1/resumes/{id}` deletes the row and, in the same transaction, enqueues `profile.delete_stored_files` through `core.jobs.enqueue`. The ids are `owner_id` and `resume_id`; the path is derived from them. The router then starts a bounded in-process drain of the `priority` queue after the response.
  - Deleting the active version leaves no version active.
- **Isolation:** another Student's id, or an unknown id, gives 404 on every `{id}` route.
- **Web:**
  - Onboarding's "Continue" after consent leads to the Resume step, which uploads and shows versions.
  - `/settings/resumes` manages versions: upload, make active, download, and delete behind a confirm dialog.
  - Server error messages are shown as plain text. Calls go only through the generated client.

**Never:**
- The client choosing a storage path. Public buckets. Long-lived or reusable download URLs. Proxying file bytes through the API on download.
- Text extraction or a Skill Profile (Story 1.6). Other Documents (Story 1.8). Purging files on account deletion (Story 1.9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First upload | valid PDF ≤ 5 MB, no Resumes | 201; stored at `<owner>/resume/<id>`; row `is_active=true`; `ResumeActivated` published | N/A |
| Later upload | valid DOCX, an active Resume exists | 201; row `is_active=false`; no event | N/A |
| Upload after deleting the active one | some versions exist, none active | new upload becomes active; event | N/A |
| Too big | 5 MB + 1 byte | 413; nothing stored; no row | problem+json, clear message |
| Wrong type | PNG renamed `.pdf`; ZIP without `word/document.xml`; empty file | 415; nothing stored | problem+json, clear message |
| Cap reached | `RESUME_MAX_VERSIONS` rows exist | 409; nothing stored | problem+json |
| DB fails after store | insert raises | stored object deleted; error propagates | 500 problem+json |
| Activate | inactive version | 200; it is the only active one; `ResumeActivated` once, before commit | N/A |
| Activate active | already active | 200; no event | N/A |
| Download | own version | `{url, expires_at}`; expiry ≤ 300 s; attachment with the version's filename | N/A |
| Other Student's id | any `{id}` route | 404; nothing changed | problem+json |
| Delete | own version | 204; row gone; delete job enqueued and run; object gone | N/A |
| Delete active | the active version | 204; no version active | N/A |
| Account deleted | account row removed | its `resumes` rows cascade-delete | N/A |
| No token | any `/resumes` route | 401 | problem+json |

**Decisions (human, 2026-11-14):**
- **Activation on upload:** an upload becomes active only when the Student has no active Resume (their first, or the first after deleting the active one). Otherwise it stays inactive until the Student chooses "Make active".
- **Version cap:** 10 versions per Student (owner chose "10 versions", 2026-11-14). It stays configurable through `RESUME_MAX_VERSIONS`, default 10.

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.4. Reuse them; don't restructure.
- `api/app/core/events.py` -- add `ResumeActivated(owner_id, resume_id)`, following `ConsentRevoked`.
- `api/app/core/errors.py` -- reuse `DomainError` and its `extra`. Add typed 413 and 415 errors (`payload-too-large` and `unsupported-media-type` slugs already exist in `_STATUS_SLUGS`). Reuse `ConflictError` and `NotFoundError`.
- `api/app/core/config.py` -- one Settings class; add the three settings.
- `api/app/core/jobs.py` -- `enqueue(session, task, key, **ids)`, `task(name, queue, retry)`, `drain(queues, timeout)`. Key format `profile.delete_stored_files:resume:<resume_id>`. For the after-response drain, mirror how `api/app/core/internal.py` runs `drain` in `BackgroundTasks`, with a short timeout on `("priority",)`.
- `api/app/main.py` -- `install_auth` wiring pattern; install the `FileStore` the same way, through `app.state` plus a dependency in `core` or `ports`. `create_app` already accepts injected fakes (see `jwks`).
- `api/app/modules/profile/*` -- stubs today (`router = APIRouter()`). Fill in models, repo, service, schemas, router and tasks. Use `tags=["profile"]` and explicit `operation_id`s.
- `api/app/modules/accounts/service.py` -- example of a service that never commits. Routers commit (see `accounts/router.py`).
- `api/migrations/versions/0005_consents.py` -- pattern for `0006_resumes.py`; update `HEAD` in `api/tests/test_migrations.py`.
- `api/pyproject.toml` -- add `python-multipart` with an exact pin (latest stable) for `UploadFile`. The import-linter vendor list already forbids `supabase` outside adapters.
- `web/src/components/consent/onboarding-consent-step.tsx` -- its "Next: your Resume" placeholder becomes the real Resume step. `consent-panel.tsx` shows the patterns: load state, 401 shows the signed-out state, an alert-dialog confirm.
- `web/src/lib/auth-paths.ts` -- `/settings/*` is already protected.

## Tasks & Acceptance

**Execution:**
- [x] `api/pyproject.toml`, `uv.lock` -- pin `python-multipart`.
- [x] `api/app/ports/file_store.py`, `api/app/adapters/supabase_storage/`, `api/tests/fakes/file_store.py` -- the port, the REST adapter, and an in-memory fake that can be told to fail.
- [x] `api/app/core/{config,events,errors}.py`, `api/app/main.py` -- settings, `ResumeActivated`, the 413/415 errors, and `FileStore` wiring.
- [x] `api/migrations/versions/0006_resumes.py` -- the table, constraints, the partial unique index, an owner index, and RLS.
- [x] `api/app/modules/profile/*` -- file sniffing, the upload/list/activate/download/delete service, the `delete_stored_files` task, and the router with the after-response drain.
- [x] `api/scripts/ensure_bucket.py`, `.github/workflows/migrate-deploy.yml`, `supabase/config.toml`, `.env.example`, `render.yaml`, `README.md` -- bucket setup and the new keys.
- [x] `api/tests/test_resumes.py`, `test_file_sniffing.py`, `test_supabase_storage_adapter.py` (an `httpx.MockTransport` checks the URLs, headers and `download` parameter) -- every matrix row.
- [x] `web/**` -- regenerate the client, add the Resume manager component, the onboarding Resume step and `/settings/resumes`, plus `node --test` tests for any pure helpers.

**Acceptance Criteria:**
- Given `ResumeActivated` is published, when a subscriber runs, then it sees the new active row and the old one inactive in the same session, before commit.
- Given the Supabase CLI stack with the bucket from `config.toml`, when a Student uploads, downloads and deletes through `web`, then the object appears in and disappears from `private/<owner>/resume/`, and the download URL stops working after it expires.

## Design Notes

- **The storage object id is the row id.** The delete job can then derive the path from `(owner_id, resume_id)` alone, so job arguments stay ids only (AD-4) after the row is gone.
- **Delete through the outbox, not inline.** The row delete and the job enqueue commit together, so a Storage outage can't leave an orphaned private file. The after-response drain removes the object within seconds when Storage is up.
- **Upload order.** The file is stored before the row is inserted, so a row never points at a missing file. A failed transaction deletes the file it stored.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Upload cleanup misses failures: `store.put` raising after the object was written (timeout), and cancellation (`CancelledError` is a `BaseException`) in put/insert/publish or at the router commit, both leave an orphaned private file | medium | `except Exception` in `service.upload_resume` and around the router commit; `put` itself is outside the try | patch |
| 2 | No early size limit: Starlette spools the whole multipart body before `file.read(MAX+1)`, so a multi-GB POST is fully received before the 413 | medium | `router.upload_resume` reads after parsing; no body-size guard; a free-tier instance can be exhausted | patch |
| 3 | `delete_stored_files` uses the default retry policy (5 attempts, ~6 min); a longer Storage outage leaves the deleted Resume's file forever | medium | `tasks.py` passes no `retry`; `DEFAULT_RETRY` has `max_attempts=5`, waits 4/16/64/256 s | patch |
| 4 | Missing tests: the router's commit-failure `discard_upload` path; concurrent uploads at "none active" and at "cap − 1" (the advisory lock) | medium | Gaps filed pre-verified: no test fails at commit or uploads concurrently | patch |
| 5 | `sanitize_filename` loses the `.pdf`/`.docx` extension when truncating, and keeps U+2028/U+2029 (Zl/Zp) | low | `name[:255]`; the category filter is only Cc/Cf/Cs; direct fix | patch |
| 6 | Adapter: a sign or bucket response that is JSON but not an object, or not JSON, raises `AttributeError`/`ValueError` instead of `FileStoreError` | low | `response.json()` then `.get`; direct isinstance guard | patch |
| 7 | The bucket sets no `allowed_mime_types`, so it isn't a second line of defence | low | `config.toml` and `ensure_bucket` set only `public` and the size limit; direct one-line config | patch |
| 8 | After a client-side precheck rejection the file input isn't cleared, so choosing the same file again does nothing | low | Early return without resetting `input.value`; direct fix | patch |
| 9 | `test_default_app_builds_storage_lazily` leaves a `LazyFileStore` installed process-wide for later tests | low | `create_app()` installs the global store; restore it in the test | patch |
| 10 | Deleting the active version publishes no event; future consumers of "the active Resume" (Skill Profile, scoring) would keep using it | medium | The frozen intent defines only `ResumeActivated`, and no subscriber exists yet. Story 1.6 must decide how "no active Resume" is handled (e.g. an `ActiveResumeCleared` event) | defer |
| 11 | No component tests for `ResumeManager` | medium | Same gap as Story 1.4: there is no component or e2e harness in `web` | defer |
| 12 | Account deletion leaves the files in Storage | false | Out of scope by intent ("Never: purging files on account deletion (Story 1.9)"); Story 1.9's `purge_files` removes `<owner_id>/` | reject |
| 13 | Storage I/O runs inside the open transaction under the per-owner lock | low | The spec mandates store-then-insert; the lock blocks only the same Student's resume operations | reject |
| 14 | An ambiguous commit failure (committed, but the ack was lost) makes `discard_upload` delete a committed row's file | low | Needs a dropped connection exactly at the COMMIT ack; a re-check adds a round trip and branches | reject |
| 15 | DOCX sniffing accepts `.docm`/`.dotx` archives | low | The file is only stored and handed back to its owner; Story 1.6 parses in a sandboxed subprocess | reject |
| 16 | An empty file gets the generic "Upload a PDF or DOCX file" message | low | Matches the frozen matrix (empty → 415) and is still clear | reject |
| 17 | `sha256` is stored but unused | low | Cheap, and useful for 1.6 extraction dedupe; no harm | reject |
| 18 | `ensure_bucket` treats a 400 lookup as "missing", and a concurrent create could fail the release | low | Concurrent releases don't happen; a failure is loud and retryable | reject |
| 19 | A missing service-role key shows up only on the first upload, not at startup | low | Lazy on purpose so OpenAPI export and tests need no key; documented in README | reject |
| 20 | The adapter opens a new `httpx.AsyncClient` per request | low | Performance only, at Phase 1 volumes | reject |
| 21 | `STORAGE_BUCKET` can differ between the GitHub variable and Render | low | Both default to `private`; no one sets them otherwise | reject |
| 22 | Onboarding has no step 3; the settings Back link goes to onboarding; refresh drops older pages; a failed refresh loses the notice | low | Placeholder navigation until Stories 1.6–1.7; minor | reject |
| 23 | "5 MB" vs 5 MiB wording; limits duplicated across files; the cap isn't shown until the 409 | low | The wording is conventional; the cap message is clear | reject |
| 24 | No test for a malformed or other-owner cursor | low | The cursor is a UUID query parameter (422 if malformed) and the list always filters by owner | reject |
| 25 | `drain_local.py` installs the store, untested; the lazy test depends on the local `.env` | low | Developer tool; the app path is covered | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack (bucket from `config.toml`), `uvicorn` and `next dev`, follow the second acceptance criterion.
