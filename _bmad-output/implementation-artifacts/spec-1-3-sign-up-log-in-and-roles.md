---
title: 'Story 1.3: Sign up, log in and roles'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: '6fab2f660b217439a7bc0484b6c14676840dcb36'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CareerLaunch has no identity yet. Nobody can sign up, the API cannot tell who is calling, and later stories need an `accounts` row, a server-enforced role and an audit trail before any Student data exists.

**Approach:** Students sign up and log in through Supabase Auth, by email and password or Google, in `web` with an `@supabase/ssr` cookie session. `web` sends the access token as a bearer token. The API verifies it, provisions the `accounts` row on first sight with role `student`, and enforces route classes: `/api/v1/*` needs a valid token, `/api/v1/admin/*` needs the admin role, and `/api/v1/public/*` is open. Admins are made only by a `promote-admin` CLI that writes `admin_audit` (AD-5, AD-14).

## Boundaries & Constraints

**Always:**
- **Token verification** lives in `core.auth`:
  - Verify signature, `exp`, `aud == "authenticated"` and `iss == <SUPABASE_URL>/auth/v1`.
  - Supported algorithms: asymmetric keys from the project's JWKS (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, cached with refresh on an unknown `kid`) and HS256 with `SUPABASE_JWT_SECRET` (legacy, local CLI).
  - Any other `alg`, including `none`, is rejected.
- **Role** is always read from `accounts`, never from token claims.
- **Account provisioning:**
  - The first valid request upserts `accounts(id = sub, email, role='student')`. Email comes from the token, is lower-cased, and is refreshed if it changed.
  - Role is never changed by provisioning. A token without `sub` or `email` gets a 401.
- **Route classes** are enforced by router-level dependencies, so a new route is protected by default:
  - `/api/v1/*` needs a valid token; missing, invalid or expired gives a 401 problem+json.
  - `/api/v1/admin/*` additionally needs `role == 'admin'`; otherwise 403 problem+json.
  - `/api/v1/public/*` needs no token.
  - Health moves to `GET /api/v1/public/health`. `render.yaml`, the web client and the tests are updated to match.
- **Endpoints:**
  - `GET /api/v1/me` returns `{id, email, role}`.
  - `GET /api/v1/admin/me` is the same for admins; it is the first admin route.
- **`promote-admin <email>`** (`uv run python scripts/promote_admin.py <email>`) sets the role to `admin`. In the same transaction it inserts `admin_audit(id, created_at, actor_id NULL = system, action='account.promote_admin', target_account_id)`. An unknown email exits 1 and changes nothing.
- **Insert-only `admin_audit`:** a trigger blocks DELETE, and any UPDATE other than setting `actor_id`/`target_account_id` to NULL. Same rule for `ops_events.owner_id`: relax its Story 1.2 UPDATE-blocking trigger to allow only `owner_id → NULL`, so that `ON DELETE SET NULL` works.
- **Foreign keys to `accounts(id)`:**
  - `admin_audit.actor_id` and `target_account_id`: `ON DELETE SET NULL`.
  - `ops_events.owner_id`: `ON DELETE SET NULL`.
  - `schedules.owner_id`: `ON DELETE CASCADE`.
  - All tables are in schema `app` with RLS via the helper.
- **Web auth:**
  - Sign-up and log-in pages offer email and password plus "Continue with Google".
  - `/auth/callback` exchanges the code for a session.
  - A Next 16 proxy (middleware) refreshes the session cookie.
  - A logged-in user lands on `/onboarding`, a placeholder page showing `GET /api/v1/me`. Logged-out users are redirected to `/login`.
  - A log-out action is included.
  - The generated API client attaches `Authorization: Bearer <access_token>` from the cookie session.
  - `web` uses `@supabase/ssr` + `@supabase/supabase-js` 2.119.0 for Auth only, never for data.
- **Secrets:** `SUPABASE_URL`, `SUPABASE_JWT_SECRET` (Render env), `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` (Vercel env) go in `.env.example`. Google client id and secret go only in Supabase Auth settings and the local `supabase/config.toml` via `env(...)`.

**Never:**
- Any API endpoint that changes a role. Role or email read from `app_metadata` / `user_metadata` claims.
- Supabase DB access from `web`. Committing OAuth secrets.
- Consent screens, onboarding steps beyond the placeholder, or account deletion (Stories 1.4–1.9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First call | valid token, no `accounts` row | 200 `/me` with role `student`; row created with `id == sub` | N/A |
| Repeat call | valid token, row exists | same row, role unchanged; email updated if changed | N/A |
| Admin claim spoof | token claims `role: admin`, DB role `student` | `/admin/me` gives 403 | problem+json `forbidden` |
| No or bad token | missing header, bad signature, wrong `aud` or `iss`, `alg=none` | 401 on `/me` and `/admin/me` | problem+json `unauthorized` |
| Expired | `exp` in the past | 401 | problem+json |
| Admin | DB role `admin` | `/admin/me` gives 200 | N/A |
| Public | no token | `/api/v1/public/health` gives 200 | N/A |
| Promote known | `promote-admin a@x.com` | role `admin` plus one `admin_audit` row | N/A |
| Promote unknown | email has no account | exit 1, nothing changed | message on stderr |
| Audit tamper | UPDATE or DELETE on `admin_audit` | rejected by the DB | trigger error |
| Unconfirmed sign-up | email/password sign-up, link not yet clicked | "Check your email" screen; no session, so no onboarding | N/A |
| Confirm link | `/auth/confirm?token_hash=…&type=email` valid | session set, redirect to `/onboarding` | invalid or expired link shows an error with a "resend" option |

**Decisions (human, 2026-11-14):**
- **Email confirmation required** for email/password sign-up, as Supabase's default. After sign-up the user sees "check your email". The emailed link goes to `/auth/confirm`, which verifies the token hash and lands on `/onboarding`. Locally, `supabase/config.toml` sets `[auth.email] enable_confirmations = true`, and mails are read in the local Mailpit inbox. Google sign-in skips confirmation.
- **Spec size:** keep the full spec as one story.

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.2. Reuse; don't restructure.
- `api/app/core/config.py` -- add `supabase_url`, `supabase_jwt_secret: SecretStr | None`.
- `api/app/core/errors.py` -- `UnauthorizedError`, `ForbiddenError` (exist).
- `api/app/main.py` -- regroup routers into `/api/v1` (auth dependency), `/api/v1/admin` (admin dependency) and `/api/v1/public`. Move health to public.
- `api/app/modules/accounts/{models,repo,service,router,schemas}.py` -- the `accounts` table, `get_or_provision()`, `promote_admin()`, `/me`, `/admin/me`.
- `api/app/core/ops.py` -- add `record_audit(session, action, *, actor_id, target_account_id)`.
- `api/migrations/versions/0002_jobs.py` -- read only; the new migration 0004 alters the `ops_events` trigger and adds the foreign keys.
- `api/scripts/check_exposed_schema.py` -- must still pass.
- `web/src/lib/api-client.ts` -- the `createClientConfig` hook; add the bearer-token auth there.
- `web/src/app/page.tsx`, `web/src/components/health-status.tsx` -- health path changes to public.
- `supabase/config.toml` -- `[auth.external.google]` enabled with `env(SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID)` / `SECRET`; site_url and redirect URLs for `http://localhost:3000`.

## Tasks & Acceptance

**Execution:**
- [x] `api/pyproject.toml`, `uv.lock` -- add `pyjwt[crypto]` (exact pin, latest stable) -- JWT verification.
- [x] `api/app/core/auth.py` -- `Actor` (id, email, role), `verify_token()`, a JWKS cache, `current_actor` / `require_admin` dependencies (the role is loaded through an accounts-service callable injected at app wiring, so `core` doesn't import `modules`) -- AD-5.
- [x] `api/app/modules/accounts/*` -- the model, repo, service, `/me` and `/admin/me` routers and schemas.
- [x] `api/migrations/versions/0004_accounts.py` -- `accounts(id uuid pk, email text not null unique, role text check in ('student','admin') default 'student', created_at)` and `admin_audit` with its trigger. Also relax the `ops_events` trigger, add the foreign keys, and apply RLS.
- [x] `api/scripts/promote_admin.py` -- the CLI.
- [x] `api/tests/test_auth.py`, `test_accounts.py`, `test_promote_admin.py` -- every matrix row, using locally signed test tokens (HS256 secret and an ES256 JWKS fake), plus "deleting an account nulls its audit/ops references and cascades schedules".
- [x] `web/**` -- `@supabase/ssr` client helpers (browser and server), `proxy.ts`, `/signup`, `/login`, `/auth/callback`, `/onboarding` (shows `/me`), log-out, and the bearer token on the generated client. Regenerate `src/api`.
- [x] `render.yaml`, `.env.example`, `supabase/config.toml`, `README.md` -- the health path, the new keys, Google provider setup steps, and how to create the first admin.

**Acceptance Criteria:**
- Given the local Supabase stack, when a user signs up with email and password in `web`, then they land on `/onboarding`, which shows role `student`, and `app.accounts` has a row whose id equals the Auth uid.
- Given a module route added under `/api/v1` without any explicit auth, when it is called with no token, then it returns 401 (protected by default).
- Given a request from an origin that is not allowed, when CORS is evaluated, then no `access-control-allow-origin` header is returned (covered by the existing test).

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` with the roles SQL; also on the Supabase CLI Postgres
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass
- `cd web && npm run lint && npm run typecheck && npm run build && npm run gen:api` -- pass, with no client diff

**Manual checks:**
- Supabase CLI stack plus `uvicorn` plus `next dev`: sign up with email and password, land on `/onboarding` showing role student, log out, log back in. Then `promote-admin` and confirm `/api/v1/admin/me` returns 200. Google sign-in needs a real Google OAuth client, so it is unverified until the owner provides one.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Open redirect: `safeNextPath` lets `/\t/evil.com` through, and the URL parser strips the tab to give `//evil.com` | high | Reproduced: `new URL("/\t/evil.com", origin)` gives `http://evil.com/`; used by `/auth/callback`, `/auth/confirm` and `authRedirects` | patch |
| 2 | Proxy matcher `"\."` in a JS string is an unescaped dot, so paths ending in `png` etc. skip the proxy | medium | String escape semantics; `/onboarding/png` bypasses session refresh and redirects | patch |
| 3 | JWKS outage: failed refresh doesn't record the attempt, so every request re-fetches serially (5 s each), with or without cached keys | medium | `_refresh` returns without touching `_fetched_at`; `_may_refresh` is keyed off it | patch |
| 4 | Malformed JWKS (`keys: null`, bad key material raising `InvalidKeyError`) gives a 500 | low | Iterating None and the uncaught exception; direct fix | patch |
| 5 | Non-string `alg` header gives a 500 (unhashable `in` frozenset) | low | `{"alg": ["ES256"]}`; direct isinstance check | patch |
| 6 | `SUPABASE_URL` defaults to localhost; a missing prod env gives a silent 401 for everyone | medium | `sync: false` on Render; the default is the CLI URL | patch |
| 7 | `admin_audit` / `ops_events` triggers allow a manual `SET actor_id = NULL`, which erases the audit subject | medium | The guard permits any update that only nulls the FKs; restrict to cascade depth | patch |
| 8 | An audit row loses its subject after account deletion | false | By design: AD-18 and the spine require SET NULL; keeping the email would retain personal data after DPDP deletion | reject |
| 9 | `promote-admin` on an existing admin writes an audit row for a change that never happened | low | Direct fix: no-op with exit 0 "already admin". Demote is not in scope | patch |
| 10 | 409 email collision locks the user out | low | Only reachable if an Auth user is re-created while the old `accounts` row survives; Story 1.9 deletes both | reject |
| 11 | No password reset ("Forgot password?") flow | medium | Not in FR-1/AC; no story covers it | defer |
| 12 | Signed-in `/login?next=` loses `next`; the custom template drops `next` | low | Minor UX | reject |
| 13 | `current_actor` commits on every request, including the read-only fast path, before the handler runs | medium | Breaks one-UoW-per-request for later write routes; commit only when provisioning wrote | patch |
| 14 | No `leeway` in `jwt.decode`; clock skew gives sporadic 401s | low | PyJWT rejects a future `iat`; direct `leeway=30` | patch |
| 15 | Test bug: `_, _, email = accounts.row(...)` takes the role as the email | medium | `row()` returns `(id, email, role)`; the admin email assertion passes vacuously | patch |
| 16 | Any `IntegrityError` is mapped to the "email linked to another account" 409 | low | Should check the constraint name; direct fix | patch |
| 17 | API routers are mounted before the admin router; a future `/{id}/me` route would shadow `/admin/*` | low | Route-order hazard; direct fix is to mount admin first | patch |
| 18 | Auth form, Google, resend and sign-out buttons have no try/catch and stay disabled forever on a throw | medium | No catch around awaited Supabase calls | patch |
| 19 | `supabase/config.toml` points to a README section "Auth setup" that doesn't exist | low | Direct doc fix (planning artifacts and the done 1.1 spec stay as historical record) | patch |
| 20 | E303 blank lines, the uncommented `cryptography` pin, raw sign-up error text | low | Style or minor | reject |
| 21 | Web open-redirect guard and proxy redirects are untested | medium | Gap filed pre-verified | patch |
| 22 | Stale-JWKS fallback is untested; the 503 response is only type-checked; the old-health-path test is weak | medium | Gap filed pre-verified | patch |
| 23 | `supabase_url` trailing-slash normalisation is untested | medium | Gap filed pre-verified | patch |
| 24 | Web bearer-token hook is untested | medium | Gap filed pre-verified; needs a Supabase client mock | defer |
| 25 | `record_event` for a deleted owner fails its FK insert, so the event is lost | medium | Implementer note; matters for Story 1.9 purge jobs | defer |
