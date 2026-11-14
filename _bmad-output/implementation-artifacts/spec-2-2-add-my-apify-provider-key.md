---
title: 'Story 2.2: Add my Apify Provider Key'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: 'd9b93b32a447027eec9fb43e919bb21982538629'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Collection (Story 2.3) runs under each Student's own Apify account, but nothing can accept, validate or safely store an Apify token yet (FR-28, AD-6, AD-12).

**Approach:** Add three pieces:
- **`core.crypto`:** AES-GCM encryption with versioned master keys.
- **`ProviderRunner` port:** an Apify adapter that validates tokens.
- **`accounts` credential store:** encrypted rows plus `GET`/`PUT`/`DELETE /provider-key`. Saving requires `provider_runs` consent and a token that Apify accepts.

`web` gets a Provider Key settings page with the setup steps.

## Boundaries & Constraints

**Always:**
- **`core.crypto`**
  - Functions: `encrypt(plaintext: str, aad: str) -> bytes` and `decrypt(blob: bytes, aad: str) -> str`.
  - Algorithm: AES-256-GCM (`cryptography` is already pinned) with a random 96-bit nonce.
  - Blob format: `b"v" + version byte(s) + b":" + nonce + ciphertext`. The key-version prefix is what makes rotation possible.
  - Master keys come from the setting `CREDENTIALS_MASTER_KEYS`, formatted as `"1:<base64-32-bytes>[,2:<…>]"`. Encryption uses the highest version; decryption uses the version in the blob.
  - A wrong `aad`, a tampered blob or an unknown version raises `CredentialError`. The message never includes the plaintext or the key.
  - Settings validate the format. A missing master key makes any store or decrypt fail with 503 `credentials-unavailable`; startup is unaffected.
- **`ProviderRunner` port** (`app/ports/provider_runner.py`)
  - `validate_token(token: SecretStr) -> ProviderAccount(user_id)`. It raises `ProviderKeyInvalid` when Apify rejects the token, or `ProviderUnavailable` on network errors, 5xx or 429.
  - The port leaves room for 2.3's run methods; don't add them now.
- **Apify adapter** (`app/adapters/apify/`)
  - Calls `GET https://api.apify.com/v2/users/me` through `httpx`, with a timeout, `Authorization: Bearer <token>` and `x-apify-integration-platform: <APIFY_INTEGRATION_PLATFORM>` (setting, default `careerlaunch`).
  - Before choosing how to classify 401/403 responses, check Apify's current API docs for how restricted or scoped tokens behave on `/v2/users/me`. Record the finding and the chosen mapping in Implementation Notes.
  - The token never appears in logs, exceptions or `repr`.
  - Fake: `tests/fakes/provider_runner.py`, configurable as valid, invalid or unavailable.
- **Table `app.provider_credentials`** (owned by `accounts`)
  - Columns:
    - `id` (uuid v7)
    - `owner_id` (FK to accounts, ON DELETE CASCADE)
    - `provider` (CHECK `'apify'`)
    - `ciphertext bytea`
    - `key_version smallint`
    - `last4 char(4)`
    - `provider_user_id text`
    - `validated_at`, `created_at`, `updated_at`
  - `UNIQUE (owner_id, provider)` and RLS.
  - The AAD is `provider_credentials:<id>:<owner_id>`, so a ciphertext copied to another row or owner fails to decrypt.
- **`accounts.service`** (no network inside a transaction)
  - `store_credential(...)` and `delete_credential(...)`.
  - `get_credential_meta(owner_id) -> {has_key, last4, validated_at}`.
  - `decrypt_credential` is used only by adapters' callers in 2.3. It returns a `SecretStr`.
- **API** (`/api/v1`)
  - `GET /provider-key` returns `{provider: "apify", has_key, last4, validated_at}`.
  - `PUT /provider-key` takes `{token}`, trimmed, 1–200 characters. Steps in order:
    1. `require_consent(provider_runs)`. Without it, 403 `consent-required` with `purpose`.
    2. Validate through the `ProviderRunner`, with no DB transaction open. Invalid gives 422 on `token`: "Apify didn't accept this token. Check that you copied it fully and that it hasn't expired." Unavailable gives 503 `provider-unavailable`.
    3. Encrypt and upsert, replacing any existing key.
    4. Publish `ProviderKeyChanged(owner_id, removed=False)` (added to `core.events`).
    5. Commit and return the meta.
  - `DELETE /provider-key` removes the row and, in the same transaction:
    - pauses the owner's collection schedules (`schedules` rows whose `kind` is in `accounts.COLLECTION_SCHEDULE_KINDS`, a constant that starts as `("listings.collect",)` and is used by 2.3);
    - publishes `ProviderKeyChanged(owner_id, removed=True)`.
    
    It returns 204, and is idempotent with no row.
  - Responses never contain the token or ciphertext.
- **Log redaction:** the JSON log formatter in `core.logging` redacts:
  - values matching `apify_api_[A-Za-z0-9]+`;
  - any `extra` field named `token`, `authorization`, `api_key` or `secret` (case-insensitive) → `"[redacted]"`.
- **Web: `/settings/provider-key`**
  - Shows the three numbered steps from the story (free Apify account; a scoped token with Run permission on approved actors only, restricted access and an expiry; paste it here), a note on USD 5 of free monthly credit, and a link to Apify's console.
  - Has a password-type input, Save, and the masked hint `••••<last4>` when a key exists, with Replace and Delete (Delete needs confirmation).
  - A 403 `consent-required` redirects to `/settings/consent?next=/settings/provider-key`; the consent page returns to `next` after a grant (a safe local path only, reusing `safeNextPath`).
  - 422 and 503 show their messages. The settings hub links to the page.

**Never:**
- Storing or returning the plaintext token; logging it; sending it to the web after save.
- Collection runs, schedules creation, caps or usage (2.3/2.4).
- Calling Apify inside a DB transaction.
- Admin access to credentials.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Save valid | consent given; fake runner accepts | 200 `{has_key: true, last4}`; one row; ciphertext ≠ token bytes; `ProviderKeyChanged` once | N/A |
| No consent | no `provider_runs` | 403 `consent-required`, `purpose`; runner not called; nothing stored | problem+json |
| Invalid token | runner raises invalid | 422 on `token` with the message; nothing stored | field problem |
| Apify down | runner raises unavailable | 503 `provider-unavailable`; nothing stored | problem+json |
| Replace | existing key; new valid token | same row id, new ciphertext and last4; old token no longer decrypts from the row | N/A |
| Delete | key plus a `listings.collect` schedule row | 204; row gone; schedule paused; event `removed=True` | N/A |
| Delete none | no key | 204; nothing changes | N/A |
| Crypto round trip | encrypt/decrypt with the right AAD | the same plaintext | N/A |
| Tamper / wrong AAD | flipped byte; another row's AAD | `CredentialError` | N/A |
| Rotation | blob from v1; keys v1,v2 | decrypts; new encryptions use v2 | N/A |
| No master key | setting unset | PUT gives 503 `credentials-unavailable`; app starts | problem+json |
| Logs | log a record with `extra={"token": …}` and a message containing `apify_api_…` | output redacted | N/A |
| Adapter | MockTransport 200/401/403/500/timeout | the documented mapping; headers include `Authorization` and `x-apify-integration-platform` | N/A |
| Isolation | another Student | sees only their own meta | N/A |
| Account deleted | account removed | credential cascades | N/A |

</frozen-after-approval>

## Code Map

Builds on Epic 1 and Story 2.1. Reuse them; don't restructure.
- `api/app/core/config.py` -- one Settings class; add `credentials_master_keys: SecretStr | None`, `apify_integration_platform`, and `apify_api_base` (default `https://api.apify.com`). Update `.env.example` and `render.yaml` (`sync: false` for the master key).
- `api/app/core/logging.py` -- the JSON formatter; add redaction there.
- `api/app/core/errors.py` -- `DomainError` with `extra`, `ConsentRequiredError`. Add `CredentialsUnavailableError` (503) and `ProviderUnavailableError` (503); the invalid token is a 422 on the `token` field.
- `api/app/core/events.py` -- add `ProviderKeyChanged(owner_id, removed)`.
- `api/app/ports/auth_admin.py`, `api/app/adapters/supabase_auth/admin.py`, `api/app/main.py` -- the port/adapter/lazy/install pattern for `ProviderRunner`, including the `create_app(provider_runner=...)` injection and `restore_ports` in tests.
- `api/app/modules/accounts/*` -- consents and the tombstone show the model, repo and service patterns; the router is mounted under `/api/v1`. `require_consent` already exists.
- `api/app/core/jobs.py` -- the `schedules` table columns (`kind`, `owner_id`, `paused`).
- `api/migrations/versions/0011_listings.py` -- latest migration; the new one is `0012_provider_credentials.py`. Bump `HEAD`. The owner-FK convention test must pass.
- `web/src/app/settings/consent/page.tsx`, `web/src/components/consent/consent-panel.tsx` -- add the `next` return after a grant. `web/src/lib/auth-paths.ts` has `safeNextPath`.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/core/crypto.py`, `api/tests/test_crypto.py` -- AES-GCM, versioned keys, AAD, errors.
- [x] `api/app/core/{config,logging,errors,events}.py` -- the settings, redaction, errors and event.
- [x] `api/app/ports/provider_runner.py`, `api/app/adapters/apify/`, `api/tests/fakes/provider_runner.py`, wiring in `app.main` and `scripts/drain_local.py` -- the port, the adapter (after checking the Apify docs), and the fake.
- [x] `api/migrations/versions/0012_provider_credentials.py` -- the table and RLS.
- [x] `api/app/modules/accounts/*` -- the credential model, repo, service and `/provider-key` routes.
- [x] `api/tests/test_provider_key.py`, `test_apify_adapter.py`, `test_log_redaction.py` -- every matrix row.
- [x] `web/**` -- regenerate the client; add the Provider Key page, the consent `next` redirect, the hub link and helper tests.
- [x] `README.md` -- how to generate `CREDENTIALS_MASTER_KEYS` (`python -c "import os,base64;print('1:'+base64.b64encode(os.urandom(32)).decode())"`), where it lives (Render env plus an offline copy), and how to rotate it.

**Acceptance Criteria:**
- Given a stored key, when the database row is read raw, then neither the token nor its first or last 8 characters appear in `ciphertext` or any other column except `last4`.
- Given the full suite, when it runs, then every earlier test still passes.

## Design Notes

- **Validate before opening a transaction.** The Apify call happens first. Only an accepted token reaches the short store transaction, so no DB connection is held during network I/O.
- **Replacing keeps the row id.** The AAD binds the row id and owner, and an upsert that keeps the id keeps that binding. Deleting and re-adding creates a new id.

## Implementation Notes

- **Apify 401/403 on `/v2/users/me` (checked 2026-11-14):** Apify's API reference for `GET /v2/users/me` documents 401 "authentication required or invalid token" (`invalid-token`; live, a garbage token returns 401 `user-or-token-not-found`) and 403 "insufficient permissions" (`insufficient-permissions`). The API-token docs say a scoped token "can access only those resources that you'll explicitly allow it to" and do not say whether a scoped token (Run on approved actors only, restricted access) may read `/users/me`. Chosen mapping:
  - 200 → valid, `user_id = data.id` (None if absent);
  - 401 → `ProviderKeyInvalid`;
  - 403 with `error.type == "insufficient-permissions"` → valid with `user_id=None` (Apify authenticated the token, it is just scoped; rejecting it would block exactly the tokens we ask for);
  - any other 403 (e.g. blocked account) → `ProviderKeyInvalid`;
  - 429, 5xx, timeouts, transport errors, other 4xx and unreadable 200 → `ProviderUnavailable`.
  Revisit once a real scoped token is tested (manual check).
- **Blob prefix:** the version is ASCII digits (`v1:`, `v12:`), so any smallint version fits.
- **Master-key format** is validated by a model-level validator: a field-level error would echo the raw key in pydantic's `input_value`.
- **Transaction boundary:** `PUT` runs `require_consent` and the master-key check, then `session.rollback()` releases the connection before Apify is called; consent is re-checked inside the short store transaction. A test asserts no `idle in transaction` session exists while the runner is called.
- **Per-owner serialisation:** store/delete lock the `accounts` row (`FOR NO KEY UPDATE`, the existing consent lock) so the upsert can keep the row id the AAD is bound to; the upsert's conflict update is guarded on that id.
- **Schedules** are owned by `core.jobs`, so pausing goes through a new `core.jobs.pause_owner_schedules(session, owner_id, kinds)`.
- **Consent `next`:** read inside `<Suspense>` (Cache Components requires it); `consentReturnPath()` accepts `next` only when `safeNextPath` returns it unchanged, and the panel `router.push`es there after any successful grant.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The second consent check runs before `lock_for_consent_change`, so a revocation committed in between is missed and a key is stored after consent was withdrawn. No test revokes consent during validation. | medium | `router.put_provider_key` calls `require_consent` before `store_credential` locks; `revoke_consent` serialises on the same lock | patch |
| 2 | Revoking `provider_runs` consent does nothing: collection schedules keep running and nothing subscribes to `ConsentRevoked` (AD-11: the owning module pauses its own work) | medium | No `ConsentRevoked` subscriber; `accounts` now owns the key and `COLLECTION_SCHEDULE_KINDS` | patch |
| 3 | Token character set unchecked. Non-ASCII (smart quotes, NBSP) gives an unhandled 500 from header encoding; control or newline characters give a misleading 503; a ≤ 4-character token makes `last4` the whole token | medium | `ProviderKeyInput` checks only length; `UnicodeEncodeError` isn't `httpx.HTTPError` | patch |
| 4 | `APIFY_API_BASE` isn't validated (plain http sends the bearer in cleartext; a malformed URL raises `InvalidURL`, which isn't an `HTTPError`, so 500) | medium | `config.py`, `runner.py` except clause | patch |
| 5 | The adapter trusts any 200: a non-dict body or missing `data` (proxy or captive portal) marks an unchecked token valid; a non-string `data.id` is stored as a repr | medium | `runner.py` 200 branch | patch |
| 6 | A `CredentialError` from `decrypt_credential` isn't a `DomainError`, so 500; meta reports `has_key` even when the row's `key_version` isn't configured | low | `service.decrypt_credential`, `get_credential_meta`; direct fixes | patch |
| 7 | Log redaction too narrow: exact field names only; no `Bearer …` pattern | low | `core/logging.py`; direct broaden (substring names, Bearer regex) | patch |
| 8 | Concurrent upsert `RuntimeError` gives a bare 500; DELETE with no row skips pausing schedules; key-version limits disagree (regex 5 digits vs smallint); `isdigit` accepts Unicode digits; `CredentialMeta.provider` hard-coded in the router | low | Direct fixes | patch |
| 9 | Consent `next` return fires for any purpose, so a `pool_contribution` grant returns early and the user loops; the pasted token is lost on redirect; Delete can double-fire while pending | low | `consent-panel.tsx`, `provider-key-manager.tsx`; direct fixes (pass the required purpose; check consent before showing the form; pending guard) | patch |
| 10 | Re-adding a key never resumes the schedules that the delete paused | medium | No schedules exist until 2.3; resuming must not override pauses from caps (2.4), so 2.3 decides | defer |
| 11 | No component tests for `ConsentPanel` `next` / `ProviderKeyManager` | medium | No `web` harness | defer |
| 12 | `validated_at` is the store time, with no re-validation path | low | Within milliseconds of the check; 2.3/2.4 detect a revoked key on runs and pause | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the local stack and a real Apify token, if the owner provides one: save it, see the last-4 hint, then delete it. Without a token, check that a garbage token gets the 422 message from real Apify.
