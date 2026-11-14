---
title: 'Story 1.8: Store and download my other Documents'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '41802a6f8e3c03dbd131318d8aab8f347f224629'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Students can keep Resumes but not their other application files, such as cover letters, transcripts and certificates. FR-26 requires these to be stored privately and downloadable only by their owner.

**Approach:**
- `profile` adds Documents next to Resumes, reusing Story 1.5's whole file path: content-type sniffing, the 5 MB limit, the `FileStore` port, the private bucket, cancellation-safe cleanup, short-lived signed downloads, and deletion through the outbox.
- Documents also accept plain text.
- `web` adds `/settings/documents`.

## Boundaries & Constraints

**Always:**
- **Types:**
  - PDF (`%PDF-`) and DOCX (a ZIP containing `word/document.xml`), detected exactly as Resumes are.
  - Plain text, accepted only when all of these hold:
    - it is non-empty;
    - it decodes as strict UTF-8 (a leading BOM is allowed);
    - it contains no NUL byte;
    - of the C0/C1 control characters, only `\t`, `\n`, `\r` and `\f` appear.
  - Plain text is stored with content type `text/plain`.
  - Anything else gives 415, "Upload a PDF, DOCX or plain-text file". More than 5 MB gives 413, "Document must be 5 MB or smaller".
  - Nothing is stored on rejection. The pre-parse body-size limit also covers `POST /api/v1/documents`.
- **Storage:**
  - Objects go to `<owner_id>/document/<document_id>`; the row id is the object id.
  - The bucket's allowed MIME types gain `text/plain`, in both `supabase/config.toml` and `ensure_bucket.py`.
  - The cap is `DOCUMENT_MAX_COUNT`, default 20; reaching it gives 409, "Delete an old document first".
- **Table `app.documents`:**
  - Columns: `id` (uuid v7), `owner_id` (→ accounts ON DELETE CASCADE), `filename` (sanitised as for Resumes), `content_type` (CHECK: the three types), `size_bytes` (1–5 MiB), `sha256`, `created_at`.
  - An index on `(owner_id, id DESC)`, and RLS via the helper.
- **API** (under `/api/v1`):
  - `POST /documents` (multipart field `file`) returns 201 with the version.
  - `GET /documents` lists newest first with a cursor (`limit` default 20, max 50).
  - `GET /documents/{id}/download` returns `{url, expires_at}`: expiry ≤ 300 s, attachment, under the stored filename.
  - `DELETE /documents/{id}` returns 204. It deletes the row and enqueues `profile.delete_document_file` (ids: `owner_id`, `document_id`; long retry, like `delete_stored_files`) in the same transaction, then starts the after-response priority drain.
  - Another Student's id, or an unknown one, gives 404 on every `{id}` route.
  - No events are published: Documents don't affect matching.
- **Reuse:** factor the shared parts of Story 1.5's upload (`check`, store, insert, cancellation-safe discard; download link; delete-and-enqueue) into helpers that Resumes and Documents both call, instead of copying them. Resume behaviour and its tests must not change.
- **Web:**
  - A `/settings/documents` page lists Documents and offers upload, download, and delete behind a confirm dialog. The client-side precheck covers type and size.
  - The `/settings` hub links to it.
  - Server messages are shown as plain text.

**Never:**
- Previewing or rendering a Document's content. Extracting text from Documents. Attaching Documents to Applications (Epic 4).
- Purging on account deletion (Story 1.9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| PDF / DOCX | valid file ≤ 5 MB | 201; object at `<owner>/document/<id>`; row | N/A |
| Plain text | UTF-8 `.txt` (with and without BOM) | 201; `content_type` `text/plain` | N/A |
| Fake text | binary with NUL; invalid UTF-8; mostly control characters; empty | 415; nothing stored | problem+json |
| Wrong type | PNG; ZIP without `word/document.xml` | 415; nothing stored | problem+json |
| Too big | 5 MiB + 1 byte, and an oversized `Content-Length` | 413 before storing | problem+json |
| Cap | `DOCUMENT_MAX_COUNT` documents | 409; nothing stored | problem+json |
| Store fails / DB fails / cancelled | each failure point | no row; no orphaned object | 500 |
| List | many Documents | newest first; cursor paging | N/A |
| Download | own Document | signed URL, expiry ≤ 300 s, attachment with its filename | N/A |
| Delete | own Document | 204; row gone; object removed by the job; idempotent if Storage is briefly down | N/A |
| Other Student / unknown id | any `{id}` route | 404; nothing changed | problem+json |
| No token | any `/documents` route | 401 | problem+json |
| Account deleted | account removed | rows cascade | N/A |
| Resumes unaffected | the existing Resume suite | still passes unchanged | N/A |

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.7. Reuse them; don't restructure.
- `api/app/modules/profile/files.py` -- `sniff_resume_type`, `sanitize_filename`, `storage_path`, `MAX_FILE_BYTES`. Add `sniff_document_type` and `TEXT`; `FileKind` gains `"document"`; add a default download name for text (`document.txt`).
- `api/app/modules/profile/service.py` -- Resume upload, list, download and delete are the code to factor. Keep its cancellation handling (`BaseException` plus a shielded discard) exactly.
- `api/app/modules/profile/router.py` -- Resume routes; `drain_priority`; the commit-failure `discard_upload` handling.
- `api/app/modules/profile/tasks.py` -- `delete_stored_files` and its long `RetryPolicy`.
- `api/app/core/limits.py`, `api/app/main.py` -- `BodySizeLimitMiddleware` is scoped to `POST /api/v1/resumes`; extend it to Documents.
- `api/app/core/config.py` -- `resume_max_versions` is the pattern for `document_max_count`. Add the key to `.env.example` and `render.yaml`.
- `api/app/adapters/supabase_storage/store.py` `ensure_bucket`, `api/scripts/ensure_bucket.py`, `supabase/config.toml` -- add `text/plain` to the allowed MIME types.
- `api/migrations/versions/0008_preferences.py` -- latest migration; the new one is `0009_documents.py`. Bump `HEAD`.
- `api/tests/test_resumes.py`, `api/tests/fakes/file_store.py` -- test patterns (fake store failure flags, concurrency, cancellation).
- `web/src/components/resumes/resume-manager.tsx`, `web/src/lib/resumes.ts` -- the UI pattern; `web/src/app/settings/page.tsx` is the hub.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/modules/profile/files.py`, `api/tests/test_file_sniffing.py` -- document sniffing (plain-text rules) and its tests.
- [x] `api/app/modules/profile/{service,repo,models,schemas,router,tasks}.py` -- the shared upload/download/delete helpers, Documents on top of them, and the `delete_document_file` task.
- [x] `api/migrations/versions/0009_documents.py`, `api/tests/test_migrations.py` -- the table and `HEAD`.
- [x] `api/app/core/{config,limits}.py`, `api/app/main.py`, bucket config files, `.env.example`, `render.yaml` -- the cap, the body limit, and `text/plain`.
- [x] `api/tests/test_documents.py` -- every matrix row.
- [x] `web/**` -- regenerate the client; add the Documents manager, `/settings/documents`, the hub link, and helper tests.

**Acceptance Criteria:**
- Given the refactor, when the whole existing Resume test suite runs, then it passes without edits to its assertions.

## Design Notes

- **Plain text is sniffed, not trusted.** There are no magic bytes, so validity means strict UTF-8 with no NUL and only whitespace controls. That rejects binaries that a client labels `.txt`.
- **Cap of 20.** Documents are smaller in number and less often re-uploaded than Resumes. 20 × 5 MB keeps a Student under 100 MB, alongside the 50 MB Resume cap, on the free tier.

## Implementation Notes

- Shared file helpers live in `profile/service.py`: `_check_file`, `_store_then_insert` (store, then insert; `BaseException` plus shielded discard), `_signed_link`, `_enqueue_file_deletion`, `_split_page`; the router shares `_read_upload` and `_commit_upload` (commit-failure/cancellation discard). Resume behaviour is unchanged and `tests/test_resumes.py` is untouched.
- Plain-text rule rejects every Unicode `Cc` character other than tab, LF, CR and FF, which also covers DEL (0x7F) besides C0/C1. A BOM-only file counts as empty (415).
- `ensure_bucket.py` exposes `ALLOWED_MIME_TYPES`; a test keeps it equal to `supabase/config.toml`. An already running local Supabase stack needs `npx supabase seed buckets` (or the script) to pick up `text/plain`.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `is_plain_text` calls `unicodedata.category` once per character (up to ~5M) synchronously in the async handler, stalling the event loop for about a second per large `.txt` | medium | A generator over the decoded string inside `check_document_upload` on the request path | patch |
| 2 | The download name keeps the user's extension whatever the sniffed type, so text containing `<script>` uploaded as `cover.html` downloads as `.html` and renders when opened; a PDF named `.txt` downloads with the wrong extension | medium | `sanitize_filename` doesn't reconcile the extension with `content_type` | patch |
| 3 | PDF/DOCX Documents with an empty or unusable filename fall back to `resume.pdf`/`resume.docx` | low | `_DEFAULT_NAMES` is keyed by content type only; direct fix: pass the kind | patch |
| 4 | The web precheck blocks valid UTF-8 text named `.md`/`.csv`/`.log` or extension-less, which the server would accept | low | `precheckDocumentFile` allows only three extensions/MIME types; direct: allow `text/*` and an empty type | patch |
| 5 | The UI hint and README never say plain text must be UTF-8, so a Windows-1252 `.txt` gets a confusing 415 | low | Direct copy fix in the hint and README (the API message is fixed by the frozen intent) | patch |
| 6 | A failed list refresh after a successful upload or delete replaces the manager with a load error | low | `refresh()` does `setLoad(await fetchPage())`; direct: keep the current view and show a message | patch |
| 7 | No component tests for `DocumentManager` | medium | Same deferral as earlier stories: no `web` harness | defer |
| 8 | API deploys could accept text before the bucket allows `text/plain` | false | `migrate-deploy.yml` runs "Ensure the private Storage bucket" in the migrate job before "Deploy api (Render deploy hook)" | reject |
| 9 | Account deletion leaves `<owner_id>/document/*` objects | false | Out of scope by intent ("Never: purging on account deletion (Story 1.9)"); Story 1.9's `purge_files` removes the prefix | reject |
| 10 | Text is stored as bare `text/plain` without a charset | low | Downloads are attachments; the bucket MIME matching on parameters is uncertain | reject |
| 11 | The UI doesn't show the 20-document cap until the 409 | low | The 409 message is clear | reject |
| 12 | Storage upload runs while the advisory lock is held | low | Same accepted trade-off as Story 1.5 (store-then-insert); per-owner only | reject |
| 13 | `sha256` unused; duplicate uploads use up slots | low | Kept for parity with Resumes; dedupe is a product choice | reject |
| 14 | Adding `text/plain` to the shared bucket loosens the Storage-level check for Resumes; the downgrade leaves bucket types and jobs | low | API sniffing remains the authority for Resumes; downgrades are local-only | reject |
| 15 | Generated client and spec missing from the review diff | false | The CI `gen:api` diff job checks the client; the spec isn't code | reject |
| 16 | Missing tests: concurrent double delete, another owner's cursor, the 413 detail per route | low | The delete is a row delete under the owner lock (the second gets 404); the cursor is owner-filtered; the per-route detail is asserted for Documents and the Resume test is unchanged | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack: upload a `.txt`, a PDF and a DOCX on `/settings/documents`, download each one, then delete them.
