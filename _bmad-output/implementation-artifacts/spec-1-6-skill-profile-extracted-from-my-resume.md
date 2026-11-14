---
title: 'Story 1.6: Skill Profile extracted from my Resume'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '1fefc64d7c426e96691e69b5246cc0c1ead2cd1b'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An active Resume (Story 1.5) is stored but nothing reads it. Matching (Epic 3) needs a Skill Profile: canonical skills plus education and experience, extracted safely from the active Resume, and correctable by the Student without losing the corrections on the next upload (FR-4, AD-8).

**Approach:**
- `ResumeActivated` enqueues a `priority` extraction job.
- The job parses the file in a sandboxed subprocess, normalises skills through a new versioned vocabulary in `core.skills`, and stores the Skill Profile.
- Students add or remove skills as `added`/`removed` sets over the extracted set, and can reset.
- `SkillProfileChanged` is published whenever the effective profile changes.

## Boundaries & Constraints

**Always:**
- **`core.skills`** (pure Python, data in the module):
  - `VOCAB_VERSION`, a vocabulary of at least 150 canonical skills (languages, frameworks, databases, cloud, tools, data/ML, CS fundamentals, common soft skills), and `normalize(text) -> set[skill_id]`.
  - Also `lookup(term) -> skill_id | None` and `label(skill_id) -> str`.
  - A `skill_id` is a lower-kebab slug that is never reused.
  - Aliases match case-insensitively on token boundaries and handle symbols: `C++`, `C#`, `.NET`, `Node.js`, `CI/CD`.
  - Required examples: "JS", "javascript", "ES6" → `javascript`.
  - No other module keeps an alias table.
- **`FileStore` gains `get(path) -> bytes`**, in the port, the Supabase adapter (authenticated download) and the fake.
- **Extraction job** `profile.extract_resume` (`priority` queue, key `profile.extract_resume:resume:<resume_id>`; ids `owner_id`, `resume_id`):
  - Enqueued by a `profile` subscriber of `ResumeActivated`, registered at app wiring, in the publisher's transaction.
  - The job is a no-op if the Resume no longer exists or is no longer active.
  - It downloads through `FileStore.get`, then runs the parser (pdfplumber 0.11.10 for PDF, python-docx 1.2.0 for DOCX, exact pins) in a child process via `asyncio.create_subprocess_exec(sys.executable, "-I", "-m", …)`, with bytes on stdin and JSON on stdout.
  - Limits:
    - 30 s wall time (the child is killed on timeout).
    - 256 MiB address space via `resource.setrlimit(RLIMIT_AS)` in the child (POSIX; skipped where `resource` is missing).
    - Extracted text capped at 200,000 characters.
    - At most 50 pages are parsed.
  - The parent then:
    - normalises skills;
    - splits education and experience by section headings ("Education", "Experience", "Work Experience", "Internships", "Projects") into at most 20 plain-text entries of at most 300 characters each per section;
    - stores the result with `vocab_version` and `extracted_at`;
    - publishes `SkillProfileChanged(owner_id)`.
  - Outcomes:
    - **Unreadable** (fewer than 50 non-whitespace characters): status `unreadable`.
    - **Crash, timeout, limit, or non-zero exit:** status `failed`. Logged with ids only, and the job does not raise, so it is not retried.
    - **Storage errors:** these raise, so the job's retry policy applies.
  - The API process never imports pdfplumber or python-docx.
- **Table `app.skill_profiles`** (one row per owner; `owner_id` PK → accounts CASCADE):
  - Columns: `resume_id` (→ resumes, ON DELETE SET NULL), `status` (`pending`, `ready`, `unreadable`, `failed`), `extracted_skills text[]`, `vocab_version`, `added_skills text[]`, `removed_skills text[]`, `custom_skills text[]`, `education jsonb`, `experience jsonb`, `extracted_at`, `updated_at`.
  - RLS via the helper.
  - The `ResumeActivated` subscriber upserts the row with `status='pending'` and the new `resume_id`, and keeps the edits.
- **Effective skills** = (`extracted` ∪ `added`) − `removed`. `custom_skills` (terms outside the vocabulary, each at most 50 characters, at most 50 of them) are stored and shown but never scored. Edits survive re-extraction until reset.
- **API** (under `/api/v1`):
  - `GET /skill-profile`: `status`, `resume_id`, `skills` (`[{id, label, source: extracted|added}]`), `removed` (`[{id, label}]`), `custom`, `education`, `experience`, `vocab_version`. Also `message`, set to the exact text below for `unreadable` and `failed`. No row gives `status: none`.
  - `PATCH /skill-profile`: `{add: [term], remove: [skill_id], remove_custom: [term]}`.
    - An `add` term is first looked up in the vocabulary. A match goes to `added_skills` and is dropped from `removed_skills`; anything else goes to `custom_skills`.
    - A `remove` of an extracted skill goes to `removed_skills`; a `remove` of an added skill takes it out of `added_skills`.
    - Publishes `SkillProfileChanged` when anything changed. Works in any status, including with no row (it creates one with `status='none'` semantics, so manual entry works without a Resume).
    - Invalid input gives 422.
  - `POST /skill-profile/reset` clears the `added`, `removed` and `custom` sets and publishes the event.
  - `GET /skills?q=` returns up to 20 vocabulary suggestions `{id, label}`, for autocomplete.
  - Upload, activate, PATCH and reset routers start the bounded after-response `priority` drain (reuse Story 1.5's `drain_priority`).
- **Message, exact text:** "We couldn't read text from this Resume. Upload a text-based PDF or add your skills manually."
- **Web:**
  - Onboarding gets step 3, Skill Profile, after the Resume step. It polls `GET /skill-profile` every 2 s while `pending`, for up to 60 s.
  - It shows the skills as removable chips with source, an add-skill input with suggestions, a custom-skills list, education and experience as plain text, the message when unreadable or failed, and "Reset to extracted" behind a confirm dialog.
  - `/settings/skills` reuses the same component.
  - All text is rendered as plain text.

**Never:**
- Parsing in the API process, or shell invocation. Storing the raw Resume text. Scoring or matching (Epic 3). Preferences (Story 1.7).
- Any LLM or network call during extraction.
- Editing education or experience (they are read-only in this story).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Activation | `ResumeActivated` | one `profile.extract_resume:resume:<id>` job on `priority`; profile `pending` | N/A |
| Text PDF | PDF naming "JS, React, PostgreSQL", with Education and Experience sections | `ready`; skills ⊇ {javascript, react, postgresql}; education/experience entries; `vocab_version`; `SkillProfileChanged` | N/A |
| DOCX | DOCX resume | same as the text PDF | N/A |
| Scanned PDF | image-only PDF (no text) | `unreadable` + exact message; manual add still works | N/A |
| Parser crash | malformed PDF that raises in the child | `failed` + same message; API healthy; job not retried | logged with ids only |
| Limits | child exceeds the time limit (test hook: a tiny timeout or a sleeping fake parser) | `failed` + message; child killed | N/A |
| Stale job | Resume deleted, or another one activated before the job runs | no-op; profile unchanged | N/A |
| Add known | `add: ["JS"]` | `javascript` in `added_skills` and effective skills; event | N/A |
| Add unknown | `add: ["Basket weaving"]` | in `custom_skills`; not in effective skills | N/A |
| Remove extracted | `remove: ["react"]` | in `removed_skills`; not effective; event | N/A |
| Re-extraction | new Resume activated | new extracted set; `added`/`removed`/`custom` still applied | N/A |
| Reset | `POST /reset` | edits cleared; effective = extracted; event | N/A |
| No-op edit | PATCH changing nothing | 200; no event | N/A |
| Bad input | 51 custom skills; a term over 50 characters; an unknown `remove` id | 422 | validation problem |
| Isolation and auth | another Student; no token | sees only their own profile; 401 without token | problem+json |
| Active Resume deleted | the source Resume is deleted | profile kept; `resume_id` null | N/A |
| Account deleted | account removed | profile cascades | N/A |

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.5. Reuse them; don't restructure.
- `api/app/core/events.py` -- `ResumeActivated` exists; add `SkillProfileChanged(owner_id)`. `subscribe`/`publish` are already there.
- `api/app/core/jobs.py` -- `task(name, queue, retry)`, `enqueue(session, task, key, **ids)`, `PRIORITY_QUEUE`, `RetryPolicy`.
- `api/app/ports/file_store.py`, `api/app/adapters/supabase_storage/store.py`, `api/tests/fakes/file_store.py` -- add `get`. Follow the adapter's `_request` / `_fail` / `_json_object` pattern and its `httpx.MockTransport` tests.
- `api/app/modules/profile/{service,repo,models,schemas,router,tasks,files}.py` -- Story 1.5's Resume code.
  - `resume_path(owner_id, resume_id)` gives the object path.
  - `router.drain_priority` is the bounded after-response drain.
  - `delete_stored_files` in `tasks.py` is the pattern for a task.
- `api/app/main.py` -- wiring: `install_auth` and the `FileStore` install. Register the `ResumeActivated` subscriber here, or from `profile.events` imported in `create_app`, so registration is explicit at startup.
- `api/migrations/versions/0006_resumes.py` -- pattern for `0007_skill_profiles.py`; bump `HEAD` in `api/tests/test_migrations.py`.
- `api/tests/test_resumes.py` -- fixtures for upload with the fake store; `tests/conftest.py` has `client`, `tokens`, `accounts`.
- `web/src/components/resumes/resume-manager.tsx` and `web/src/components/consent/onboarding-consent-step.tsx` -- the onboarding step flow (step state lives in the consent step component). `consent-panel.tsx` shows the load, 401 and alert-dialog patterns.

## Tasks & Acceptance

**Execution:**
- [x] `api/pyproject.toml`, `uv.lock` -- pin `pdfplumber==0.11.10` and `python-docx==1.2.0`.
- [x] `api/app/core/skills.py`, `api/tests/test_skills.py` -- the vocabulary, `normalize`, `lookup`, `label` and `VOCAB_VERSION`; tests for the aliases, the symbol cases and no false positives inside words (e.g. "Java" in "JavaScript", "R" in words, "Go" in "good").
- [x] `FileStore.get` -- port, adapter and fake, plus an adapter test.
- [x] `api/app/modules/profile/extract_worker.py` (child entry point), and `extraction.py` (parent: run the child with its limits, then split sections) -- with tests that use generated PDF and DOCX fixtures (build them in the tests; pdfplumber's dependencies or python-docx can write them, or keep tiny committed fixtures under `api/tests/fixtures/`).
- [x] `api/migrations/versions/0007_skill_profiles.py`, profile models, repo, service, schemas, router and tasks -- the subscriber, the job, the GET/PATCH/reset/skills routes, and drains after upload, activate, PATCH and reset.
- [x] `api/tests/test_skill_profile.py` -- every matrix row.
- [x] `web/**` -- regenerate the client; add the Skill Profile component, onboarding step 3 and `/settings/skills`; add `node --test` tests for the pure helpers.

**Acceptance Criteria:**
- Given a running API, when the extraction child is killed by the time or memory limit, then `GET /api/v1/public/health` still answers 200 and later requests succeed.
- Given the Supabase CLI stack, when a Student uploads a text PDF in `web`, then within a few seconds the onboarding Skill Profile step shows the extracted skills.

## Design Notes

- **Active Resume deleted.** The profile is kept, with `resume_id` NULL (FK `SET NULL`), and the UI says the source Resume was deleted. Skills are the Student's profile data rather than file content. The next activation re-extracts. No new event is needed because `profile` owns both tables. This settles the Story 1.5 deferral.
- **Parse in the child, interpret in the parent.** Only the risky parsing runs sandboxed. Section splitting and normalisation stay in-process, where they are pure and testable.
- **Parser failures aren't retried.** A malformed file fails the same way every time. Storage failures are transient, so they do retry.

## Implementation Notes

- **Child invocation is `python -I <path>/extract_worker.py`, not `-I -m app...`.** `-I` drops the working directory from `sys.path` and `uv` does not install the project (`package = false`), so `-m app.modules.profile.extract_worker` cannot be imported in isolated mode. The worker is therefore self-contained (stdlib + the parser only, never `app`), and is run by path with `-I` kept. Still `asyncio.create_subprocess_exec`, list argv, no shell, bytes on stdin, JSON on stdout, stderr discarded.
- **Windows fallback.** psycopg needs the selector event loop on Windows, which has no subprocess support; on `NotImplementedError` the same argv runs via `subprocess.run(timeout=...)` in a worker thread (the child is still killed on timeout). Linux (CI, Render) uses the asyncio path.
- **RLIMIT_AS** is applied in the child after the parser import and before reading stdin. Verified on Linux: parsing works at 256 MiB; a 16 MiB limit makes the child exit non-zero (`failed`).
- **`status` is nullable.** A row created by a PATCH without any Resume has `status = NULL`, reported as `none`.
- **`subscribe` is idempotent** for the same (event, handler), so `create_app()` can call `profile.events.register()` every time (one app per test).
- **Unknown `remove` id** = not a vocabulary id (422). A vocabulary id that is not in the profile is a no-op.
- **Reset** with no edits (or no row) writes nothing, publishes nothing and starts no drain. `GET` returns `has_edits` so the UI shows Reset even when an added skill is also extracted.
- **Deleting the source Resume while `pending`** settles the profile to `status = NULL` (`none`) in the same transaction, keeping the edits.
- **Child environment** is minimal (`PATH`, plus `SYSTEMROOT` on Windows): no API secrets reach the parser. The child also sets `RLIMIT_CPU` (= the parse timeout); `setrlimit` failures (e.g. `RLIMIT_AS` on macOS) are ignored.
- **"Go", "R", "C"** match only case-sensitively, not before `&`, `-`, `'`, not after "grade "/"class ", and not inside a longer alias; `VOCAB_VERSION` bumped to `2026.11.2`.
- **Vocabulary ids** avoid common words where the id itself would match prose: `spring-framework`, `microsoft-excel`; generic aliases such as "rest", "cv", "sh", "tf" are not included.
- **Import guard.** A new import-linter contract forbids `pdfplumber`, `docx`, `pdfminer` and `pypdfium2` everywhere in `app` except `extract_worker`; tests also assert neither parser is ever imported in the test/API process.
- **Parse timeout is 20 s**, inside the after-response drain's 25 s work budget (30 s minus the 5 s worker grace), so the parser fails cleanly before the drain cancels the job.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The parser child inherits the API's whole environment (service-role key, DB URL), so a parser exploit could read secrets | high | `create_subprocess_exec` / `subprocess.run` are called without `env=`; `-I` ignores only `PYTHON*` variables | patch |
| 2 | Deleting the source Resume while its extraction is pending leaves the profile `pending` forever, and the UI polls and then stalls | medium | The FK nulls `resume_id`; the job finds no target and returns; `store_extraction` filters on `resume_id` | patch |
| 3 | The parse limit (30 s) exceeds the after-response drain's work budget (30 − 5 s grace), so a slow parse is cut off by the drain, not the parser | medium | `router.PRIORITY_DRAIN_TIMEOUT_SECONDS=30`, `jobs.WORKER_SHUTDOWN_GRACE_SECONDS=5`, `extraction.TIMEOUT_SECONDS=30` | patch |
| 4 | Common short aliases ("go", "R", "C") match ordinary prose ("go live", "R&D", "Grade C", "C-suite") and add false skills | medium | `normalize` is case-insensitive over the whole text; tests cover only substring cases | patch |
| 5 | An edit and an extraction can interleave, so a removal is computed against a stale extracted set and dropped | low | `save_extraction` doesn't take `lock_owner_skill_profile`; direct fix: take the same lock | patch |
| 6 | Hidden edits: an added skill that is also extracted shows as "extracted", so `hasEdits` is false and Reset is hidden although reset would change something | low | The router labels by `extracted_skills` first; `hasEdits` looks only at `source==="added"`. Fix: a server-provided `has_edits` flag | patch |
| 7 | Reset with no edits still publishes `SkillProfileChanged` and drains | low | `reset_skill_profile` always writes; `edit` avoids no-ops; direct guard | patch |
| 8 | One failed poll replaces the whole editor with a permanent error | low | `setLoad(next)` on error during polling; keep the previous state and retry next tick | patch |
| 9 | `setrlimit(RLIMIT_AS)` raising (macOS) makes every extraction fail; no CPU limit | low | No try/except around `setrlimit`; direct: ignore `ValueError`/`OSError` and add `RLIMIT_CPU` | patch |
| 10 | Section detection misses common heading variants ("Professional Experience", "Work History", "Employment", "Internship", "Academic Projects", "Academic Background", "Education & Qualifications") | low | `SECTION_HEADINGS` is exact-match only; direct: extend the map, plus tests | patch |
| 11 | Tests don't check what they claim: the page cap is untested (all fixtures are one page), the child's character cap is masked by the parent's cut, suggestion ordering has an always-true assert, DOCX tables are never exercised, and two fixtures are unused | medium | Gaps filed pre-verified | patch |
| 12 | Web: the add-skill input allows 80 characters vs the server's 50; `aria-controls` points at a missing list | low | Direct attribute fixes | patch |
| 13 | `FileStore.get` failing until retries run out leaves the profile `pending` | medium | Needs a Storage outage longer than the retry window; settled by an exhausted-job hook in `core.jobs`, which doesn't exist yet | defer |
| 14 | `vocab_version` is stored but nothing re-normalises on a vocabulary bump; custom skills are never promoted | medium | AD-8 says a vocabulary bump enqueues re-extraction; no bump exists yet; the first vocabulary change must add the backfill job | defer |
| 15 | No component tests for `SkillProfileEditor` | medium | Same gap as Stories 1.4 and 1.5: there is no component or e2e harness | defer |
| 16 | Concurrent parser children have no cap and could exhaust memory | false | `drain()` holds a cross-process `pg_try_advisory_lock` and the worker runs with `concurrency=1`, so at most one child exists at a time | reject |
| 17 | Transient timeout or spawn failures aren't retried; no re-extract action; `failed` shares the unreadable message | low | The frozen intent requires parser failures to be `failed` with the same message and not retried; a re-extract action is new surface | reject |
| 18 | `failed`/`unreadable` clears the previous Resume's extracted skills | false | The profile reflects the active Resume; the old Resume's skills shouldn't survive a switch | reject |
| 19 | `removed_skills` keeps ids the new Resume lacks | low | Harmless; removal survives by design until reset | reject |
| 20 | The 422 `loc` uses a string index, not an int | low | Cosmetic; the client shows only `msg` | reject |
| 21 | Upload always drains, even when inactive | low | A cheap no-op drain | reject |
| 22 | Onboarding step 3 dead end; settings Back link; no combobox keyboard navigation | low | Navigation placeholder until Story 1.7; a11y polish | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack, `uvicorn` and `next dev`, follow the second acceptance criterion; then upload a scanned PDF and see the exact message.
