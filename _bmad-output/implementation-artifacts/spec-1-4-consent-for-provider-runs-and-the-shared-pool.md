---
title: 'Story 1.4: Consent for provider runs and the shared pool'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '8a1790cac8c86b6e36d5e45288d2911b8521a1dc'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing records whether a Student agreed to runs under their own Apify account, or to their Listings entering the shared pool. Later epics need one server-side gate to check before enqueueing or running that work, and one event to react to when consent is withdrawn (FR-2, FR-28, AD-11).

**Approach:**
- `accounts` owns versioned, timestamped consent records per purpose, with `require_consent(owner_id, purpose)` as the only check.
- The API serves the plain-language consent text and records grants and revocations.
- Revoking publishes `ConsentRevoked(owner_id, purpose)` in the same transaction through a new `core.events`.
- `web` shows the consent screen as the first onboarding step and again under settings.

## Boundaries & Constraints

**Always:**
- **Purposes:** Phase 1 has `provider_runs` and `pool_contribution`. Each has its own consent text and a version string (e.g. `2026-11-14`). The text is owned by `accounts` and served by the API, so a stored version always names text the server actually showed.
- **Records:**
  - Table `app.consents(id uuid v7, owner_id → accounts ON DELETE CASCADE, purpose, version, granted_at, revoked_at NULL)`.
  - At most one active (`revoked_at IS NULL`) row per owner and purpose, enforced by a partial unique index.
  - Granting inserts a new row. Revoking sets `revoked_at` on the active row. History is kept.
  - RLS is applied via the helper.
- **Gate:** `accounts.service.require_consent(session, owner_id, purpose)` passes only when an active row exists whose `version` equals the purpose's current version. Otherwise it raises `ConsentRequiredError`, which maps to 403 problem+json with type `/problems/consent-required` and the `purpose` in the body.
- **Events:** `core.events` defines typed, frozen-dataclass events (this story adds `ConsentRevoked(owner_id, purpose)`) with `subscribe(event_type, handler)` and `await publish(session, event)`. Subscribers run synchronously, in registration order, inside the caller's transaction, before commit. If a subscriber raises, the whole unit of work rolls back.
- **API (under `/api/v1`, token required):**
  - `GET /consents` returns three things:
    - for each purpose: `purpose`, `title`, `body` (plain-text paragraphs), `version`, and `status` (`granted`, `revoked`, `none` or `outdated`), plus `granted_at` and `revoked_at`;
    - a `rights` notice: India DPDP Act rights, including deletion within 30 days;
    - a `notice_version`.
  - `PUT /consents/{purpose}` takes `{version}`. If `version` matches the current one, it grants: idempotent if already active on that version, otherwise it supersedes an outdated active row. If `version` doesn't match, it returns 409 `consent-version-mismatch` and nothing is written.
  - `DELETE /consents/{purpose}` revokes. When an active row exists, it sets `revoked_at` and publishes `ConsentRevoked` in the same transaction, and returns 204. When nothing is active, it returns 204 without writing anything or publishing.
  - An unknown purpose gives 422.
  - Only routers commit.
- **Consent text covers:**
  - `provider_runs`: runs go under the Student's own Apify account and are charged to it by Apify, and LinkedIn's terms restrict scraping.
  - `pool_contribution`: Listings collected under the Student's key are shared with other Students whose Preferences match. Without this consent, those Listings stay visible only to the Student.
  - Rights: the DPDP Act rights (access, correction, erasure, withdrawing consent at any time, grievance), with deletion within 30 days.
- **Web:**
  - `/onboarding` becomes the consent step: each purpose shows its text with an "I agree" action, the rights notice is shown below, and "Continue" proceeds.
  - `/settings/consent` reuses the same component. Revoking requires a confirm dialog.
  - Text is rendered as plain text. Calls go only through the generated client.

**Never:**
- Consent flags on `accounts` or in token claims; any other consent check besides `require_consent`.
- Callers of `require_consent` (jobs, provider runs, the pool), Provider Key entry, or cancelling jobs on revoke. Those belong to later epics, which subscribe to `ConsentRevoked`.
- The `linkedin_link`, `outreach` and `llm_drafting` purposes (Phase 2).
- Admin access to consent records.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First view | no rows | `GET /consents`: both purposes have `status: none`, with text, version and the rights notice | N/A |
| Grant | `PUT provider_runs {version: current}` | 200; one active row with version and `granted_at` | N/A |
| Grant again | already active on the current version | 200; no new row | N/A |
| Stale text | `PUT` with an old version | 409 `consent-version-mismatch`; nothing written | problem+json |
| Revoke | active row, `DELETE` | 204; `revoked_at` set; `ConsentRevoked` delivered once to a subscriber before commit | N/A |
| Revoke, none active | no active row | 204; no write; no event | N/A |
| Subscriber fails | subscriber raises on `ConsentRevoked` | revocation rolled back; row still active | 500 problem+json |
| Re-grant | after revoke | new active row; old row kept, revoked | N/A |
| Gate pass | active row on the current version | `require_consent` returns | N/A |
| Gate fail | none, revoked, or outdated version | `ConsentRequiredError`; 403 `consent-required` with `purpose` | problem+json |
| Isolation | Student B's calls | see and change only B's rows | N/A |
| Unknown purpose | `PUT /consents/outreach` | 422 | validation problem |
| No token | any `/consents` route | 401 | problem+json |
| Account deleted | account row removed | its consent rows cascade-delete | N/A |

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.3. Reuse them; don't restructure.
- `api/app/core/errors.py`: `DomainError` subclasses. Add `ConsentRequiredError` (403, slug `consent-required`) and `ConsentVersionMismatchError` (409). The `purpose` must appear in the problem body, so add optional `extra` support to `DomainError` and `_domain_exception`.
- `api/app/core/events.py` (new): the event registry and `ConsentRevoked`. `core` must not import modules; import-linter enforces this.
- `api/app/core/ops.py`: `uuid.uuid7()` is the id pattern to follow.
- `api/app/modules/accounts/{models,repo,service,router,schemas}.py`: extend these.
  - Service: takes the session and never commits.
  - Router: mounted via `include_api_router` in `api/app/main.py`, so it is already token-protected. Routers commit.
  - Add consent text constants in `accounts/consent_text.py`.
- `api/migrations/versions/0004_accounts.py`: follow its pattern (`create_app_table_rls`, the FK to `app.accounts`, the downgrade). The new migration is `0005_consents.py`.
- `api/tests/conftest.py`: reuse the `client`, `tokens` and `accounts` fixtures. Update `HEAD` in `api/tests/test_migrations.py` to `0005_consents`.
- `web/src/app/onboarding/page.tsx`: currently a placeholder showing `MeCard`. Replace it with the consent step and keep `SignOutButton`.
- `web/src/lib/api-client.ts`: the generated client already sends the bearer token. Regenerate `web/src/api` with `npm run gen:api`.
- `web/src/proxy.ts`: confirm that `/settings/*` is protected like `/onboarding`.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/core/events.py` -- `Event` base, `ConsentRevoked`, `subscribe`, `publish`, and a test-only `clear_subscribers` -- AD-20 events.
- [x] `api/app/core/errors.py` -- the two errors and `extra` support -- typed 403/409.
- [x] `api/migrations/versions/0005_consents.py` -- the table, a purpose CHECK, the partial unique index, the owner index, RLS, and the downgrade.
- [x] `api/app/modules/accounts/*` -- the model, repo, `consent_text.py`, the service functions (`list_consents`, `grant_consent`, `revoke_consent`, `require_consent`), schemas, and the three routes.
- [x] `api/tests/test_consents.py`, `test_events.py` -- every matrix row, plus the `require_consent` unit cases.
- [x] `web/**` -- the consent component, `/onboarding` (consent step), `/settings/consent` with a revoke confirm, the regenerated client, and a link from onboarding to settings.
- [x] `README.md` -- a short note on consent purposes and how to bump a consent version.

**Acceptance Criteria:**
- Given a revoke, when `ConsentRevoked` is published, then the subscriber sees the row already revoked in the same session, and no commit has happened yet.
- Given a new consent version in `consent_text.py`, when a Student's active row has the old version, then `GET` shows `outdated` and `require_consent` raises until they grant again.
- Given the web app on the local stack, when a signed-in Student agrees to both purposes on `/onboarding` and later revokes one on `/settings/consent`, then the status shown updates each time.

## Design Notes

- **Consent is skippable in onboarding.** The PRD makes a Provider Key optional and time-to-value matters, so "Continue" works without consenting. The gate blocks the work itself, not the UI.
- **Purposes are independent.** `pool_contribution` doesn't require `provider_runs`, because it has no effect until runs happen.
- **Version check on grant.** This stops a Student from agreeing to text they never saw after a deploy changed it.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Superseding an outdated grant sets `revoked_at` exactly like a withdrawal, so the consent history shows a revocation the Student never made | medium | `grant_consent` calls `repo.revoke_active_consent`; no column distinguishes the two cases | patch |
| 2 | A 401 from grant or withdraw shows "Try again" instead of the signed-out "Log in again" state | low | `consent-panel.tsx` handles 401 only in `fetchOverview`; direct branch fix | patch |
| 3 | The `Consent.id` ORM column has no default, so any `add()` path hits a NOT NULL error | low | Only the explicit-id insert exists today; one-line `default=uuid.uuid7` | patch |
| 4 | Concurrent grants (double submit) untested; losing the account lock would let a 500 through | medium | Gap filed pre-verified: no concurrent test in `test_consents.py` | patch |
| 5 | Missing tests: withdrawing an `outdated` consent publishes `ConsentRevoked`; GET shows `granted` after grant → revoke → grant; unused `tokens` fixture in `test_no_token_is_401` | medium | `distinct_on` ordering and the outdated-revoke path are unexercised | patch |
| 6 | `web/src/lib/consent.ts` duplicates the status union instead of using the generated type, so it can drift | low | Direct fix with a type-only import (erased under `node --test`) | patch |
| 7 | Web panel behaviour (409 reload, withdraw shown only in settings) has no executed test | medium | Gap filed pre-verified; there is no component or e2e harness in `web` yet | defer |
| 8 | Rights notice has no grievance or contact details, and promises account deletion before Story 1.9 ships it | medium | DPDP notices must say how to exercise rights; no contact exists in the repo; deletion arrives in Story 1.9 within this epic | defer |
| 9 | Consent texts don't say what happens to Listings already in the pool, or to runs in progress, after withdrawal | medium | Pool and run cancellation behaviour is defined in Epic 2; the text must be updated (with a version bump) when it is | defer |
| 10 | `require_consent` is an unlocked read; work can pass the gate just before a withdrawal commits | maybe-false | Story 1.4 has no callers; settled by how Epic 2 callers re-check inside the job at the side effect. Would be medium if true | defer |
| 11 | `current_text` raises `KeyError`, not `ValueError`, for an enum member with no text | low | Only reachable if a developer adds a purpose without its text, and GET tests would fail at once | reject |
| 12 | After a 409, a failed refresh replaces the message with the error view | low | The error view is itself an error message; the user is not misled | reject |
| 13 | The same handler can be subscribed twice and run twice | false | Python imports a module once; registration at import cannot repeat | reject |
| 14 | A subscriber calling `commit()` breaks rollback | low | Developer misuse, against the documented contract; adding a guard adds complexity | reject |
| 15 | DELETE returns 204 but PUT returns 404 for a missing account; 404 undeclared in OpenAPI | false | `current_actor` provisions the accounts row before any consent route, so the account always exists | reject |
| 16 | Rows don't store `notice_version`, source or actor; grants and withdrawals aren't audit-logged | low | The frozen intent fixes the record shape (purpose, version, granted_at, revoked_at); the rights notice needs no agreement | reject |
| 17 | API lacks `agreed_version`; `outdated` items hide the agreed date | low | Adds public surface; the badge already says "please review" | reject |
| 18 | A version bump blocks the gate without any event | false | AD-11 has jobs re-check the gate inside the job, so the work stops there; no subscriber action is needed | reject |
| 19 | `/settings` index returns 404; "Back" always goes to `/onboarding` | low | Placeholder navigation until the settings hub exists; minor | reject |
| 20 | Onboarding "Continue" is lost on reload; `MeCard` removed from `/onboarding` | low | Placeholder step until Story 1.5; role info isn't needed by Students | reject |
| 21 | No startup step registers event subscribers | low | No subscriber exists yet; the first subscribing story wires registration | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack, `uvicorn` and `next dev`: sign in, grant both purposes on `/onboarding`, revoke one on `/settings/consent`, and check that `app.consents` rows match.
