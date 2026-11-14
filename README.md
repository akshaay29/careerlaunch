# CareerLaunch

Monorepo for CareerLaunch: a FastAPI modular monolith (`api/`), a Next.js UI (`web/`) and
Supabase CLI config (`supabase/`). The architecture rules live in
`_bmad-output/planning-artifacts/architecture/.../ARCHITECTURE-SPINE.md`.

| Path | What |
| --- | --- |
| `api/` | FastAPI 0.162 on Python 3.14, async SQLAlchemy 2.1 + psycopg 3, Alembic. Managed with `uv`. |
| `api/app/core/` | Kernel: config, db (search_path hook), problem+json errors, JSON logging, jobs (outbox, drain, `scheduler_tick`), ops (`ops_events`), `/internal` routes. |
| `api/app/modules/` | `accounts ← profile ← listings ← matching ← tracker` (import-linter enforced). |
| `api/app/ports/`, `api/app/adapters/` | Ports and adapters; vendor SDKs only in `adapters/`. |
| `api/migrations/` | Alembic owns **all** DDL. Every table lives in schema `app` with RLS. |
| `web/` | Next.js 16 App Router, Tailwind CSS, shadcn/ui. Talks to the api only via `web/src/api/` (generated). |
| `supabase/` | CLI config only. Exposed schemas `public, graphql_public` must stay empty. |

## Prerequisites

- Python 3.14 via [uv](https://docs.astral.sh/uv/) 0.13+
- Node.js 26 (`.nvmrc`) and npm
- Docker (for the Supabase CLI stack, or a plain `postgres:17` for tests)

## Local setup

```bash
# 0. Config: copy the template (defaults already target the Supabase CLI stack)
cp .env.example .env

# 1. Supabase CLI stack (Postgres on :54322, Auth on :54321, Studio on :54323, Mailpit on :54324)
npx supabase start
npx supabase status   # note the anon key, the service_role key and the JWT secret
#    -> .env:           SUPABASE_JWT_SECRET=<JWT secret>
#                       SUPABASE_SERVICE_ROLE_KEY=<service_role key>   (Storage, api only)
#    -> web/.env.local: NEXT_PUBLIC_API_URL=http://localhost:8000
#                       NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
#                       NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>

# 2. api
cd api
uv sync
uv run alembic upgrade head          # creates + locks down schema `app`
uv run uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000/api/v1/public/health  => {"status":"ok"}

# 3. web (new terminal)
cd web
npm ci
npm run dev
# -> http://localhost:3000 shows "API health: ok"; /signup creates an account
```

> **Windows:** psycopg's async driver cannot use the default Proactor event loop. Tests and
> Alembic select a selector loop automatically; for `uvicorn` add
> `--loop asyncio:SelectorEventLoop`. Linux/macOS, CI and Render need nothing special.

### Everyday commands

| Command (from `api/`) | Purpose |
| --- | --- |
| `uv run pytest` | Tests (needs a migrated Postgres at `DATABASE_URL`) |
| `uv run lint-imports` | Module order, kernel isolation, vendor-SDK isolation |
| `uv run python scripts/check_exposed_schema.py` | AD-1 check: nothing in `public`/`graphql_public`, RLS on, no grants to `anon`/`authenticated` |
| `uv run python scripts/drain_local.py` | Run one job drain locally (`--every 300` to loop every 5 minutes, like pg_cron) |
| `uv run python scripts/promote_admin.py <email>` | Make an existing account an admin (audited; see "Auth and roles") |
| `uv run alembic revision -m "..."` | New migration. Call `migrations.helpers.create_app_table_rls(table)` for every new table. |

| Command (from `web/`) | Purpose |
| --- | --- |
| `npm run gen:api` | Export OpenAPI (`web/openapi.json`) and regenerate `web/src/api/`. **Commit both.** Never hand-edit `src/api/`. |
| `npm run lint` / `npm run typecheck` (`next typegen && tsc --noEmit`) / `npm run build` | Same checks as CI |

### Running the tests against plain Postgres (what CI does)

```bash
docker run -d --name cl-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:17
docker exec -i cl-pg psql -U postgres < api/tests/sql/supabase_roles.sql
cd api
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres
uv run alembic upgrade head && uv run pytest
```

## Background jobs (AD-4)

The host sleeps and there is no always-on worker, so all background work is a durable job:

- **Enqueue** only with `app.core.jobs.enqueue(session, task, key, run_at=None, queue=..., **ids)`.
  It writes a `job_outbox` row in the caller's transaction (rollback = no job). Arguments are
  ids only (`uuid.UUID` / `int`); the key `<module>.<task>:<entity_type>:<id>[:<scope>]` is the
  Procrastinate queueing lock, so a duplicate pending key yields one job.
- **Define** tasks only with `@app.core.jobs.task("<module>.<task>", queue=..., retry=...)`
  (async functions; default retry: exponential backoff, 5 attempts). Each run writes
  `job.succeeded` / `job.failed` to `ops_events`. `app.modules` may not import `procrastinate`.
  **Delivery is at-least-once** (retries, stalled-job re-queues, outbox re-defers after a
  crash), so every task must be idempotent. `enqueue` rejects task names that aren't registered.
- **Recurring work** is a `schedules` row (`kind` = task name); never `@app.periodic`.
- **Drain** (`POST /internal/jobs/drain`, 202, work runs after the response): advisory lock →
  flush outbox → `scheduler_tick` → retry stalled jobs → worker (`priority`, then `default`,
  `wait=False`), all under `DRAIN_TIMEOUT_SECONDS` (≤ 240, worker shutdown grace included); only a
  completed drain advances `drain_state.last_drain_at`.
  `GET /internal/health` returns 503 when the last drain is older than 15 minutes.
- **Triggers:** pg_cron calls the drain every 5 minutes (migration `0003`, via pg_net, secret
  from Vault). `.github/workflows/health-drain.yml` checks health every 15 minutes and, if
  stale, runs a backup drain and fails (GitHub's failure email is the alert). Locally, run
  `uv run python scripts/drain_local.py` (once, or `--every 300`).

`/internal/*` requires `Authorization: Bearer <DRAIN_SECRET>` (or `DRAIN_SECRET_PREVIOUS`
during rotation) and is never in the OpenAPI schema.

### Operator setup: drain secret (one-time, and on rotation)

1. Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. **Render env:** `DRAIN_SECRET=<secret>`.
3. **GitHub `production` environment secrets:** `DRAIN_SECRET=<secret>` and
   `API_BASE_URL=https://<your-render-service>.onrender.com`.
4. **Supabase Vault** (SQL editor; run once, placeholders only — never commit real values):

   ```sql
   select vault.create_secret('https://<your-render-service>.onrender.com/internal/jobs/drain', 'drain_url');
   select vault.create_secret('<secret>', 'drain_secret');
   -- to change a value later:
   -- select vault.update_secret((select id from vault.secrets where name = 'drain_secret'), '<new secret>');
   ```

   Then run the migrations (release workflow). Migration `0003` enables `pg_cron` + `pg_net`
   and schedules `careerlaunch-drain` (`*/5 * * * *`, `timeout_milliseconds := 90000`), which
   reads both values from `vault.decrypted_secrets` at run time. On plain Postgres (CI) it
   only raises a NOTICE. Check with `select jobname, schedule from cron.job;`.

**Rotation:** set `DRAIN_SECRET_PREVIOUS=<old>` and `DRAIN_SECRET=<new>` on Render, update the
Vault `drain_secret` and the GitHub secret, then clear `DRAIN_SECRET_PREVIOUS`.

## Auth and roles (AD-5, Story 1.3)

- **Sign-in** is Supabase Auth (email + password, or Google) in `web`, with an `@supabase/ssr`
  cookie session. `web/src/proxy.ts` refreshes the session cookie on every page request,
  sends signed-out visitors of `/onboarding` and `/settings/*` to `/login` and signed-in visitors of
  `/login`/`/signup` to `/onboarding`. `web` uses Supabase **only** for Auth.
- **Email confirmation is required.** After sign-up the user sees "Check your email". The link
  (template `supabase/templates/confirmation.html`) opens `/auth/confirm?token_hash=...`,
  which sets the session and lands on `/onboarding`; a bad or expired link shows
  `/auth/error` with a "Resend confirmation email" form. Locally, read the mails in Mailpit at
  <http://127.0.0.1:54324>. Google and other OAuth redirects come back to `/auth/callback`.
- **The API** verifies every `Authorization: Bearer <access token>` (`app/core/auth.py`):
  signature (ES256/RS256 keys from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, cached, or
  HS256 with `SUPABASE_JWT_SECRET`), `exp`, `aud = authenticated`, `iss = <SUPABASE_URL>/auth/v1`.
  The first valid request creates the `accounts` row (`id` = Auth uid, role `student`). The
  role is always read from `accounts`, never from token claims. The generated web client sends
  the token automatically for operations that declare the bearer scheme.
- **Route classes** are router-level dependencies in `app/main.py`, so new routes are protected
  by default: `include_api_router` (`/api/v1/*`, valid token, else 401),
  `include_admin_router` (`/api/v1/admin/*`, role `admin`, else 403),
  `include_public_router` (`/api/v1/public/*`, no token; health is `GET /api/v1/public/health`),
  `include_internal_router` (`/internal/*`, drain secret).
- **Admins** are created only by the CLI; no API changes a role:

  ```bash
  cd api
  uv run python scripts/promote_admin.py person@example.com   # DATABASE_URL picks the database
  ```

  The person must have signed up and opened the app once (so the `accounts` row exists). The
  role change and an `admin_audit` row (`account.promote_admin`, actor NULL = system) commit
  together; an unknown email exits 1 and changes nothing. `admin_audit` is insert-only (DB
  trigger). For the first prod admin, run it with `DATABASE_URL` set to the session-pooler URL.

### Google sign-in setup

1. Google Cloud Console → APIs & Services → Credentials → *Create OAuth client ID* (type: Web
   application). Authorized redirect URIs: `https://<project-ref>.supabase.co/auth/v1/callback`
   (hosted) and `http://127.0.0.1:54321/auth/v1/callback` (local CLI).
2. **Hosted:** Supabase dashboard → Authentication → Sign In / Providers → Google: enable, paste
   the client id and secret. Nothing goes in the repo, Render or Vercel.
3. **Local:** put `SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID=...` and
   `SUPABASE_AUTH_EXTERNAL_GOOGLE_SECRET=...` in `supabase/.env` (gitignored), then
   `npx supabase stop && npx supabase start`. `supabase/config.toml` reads them with `env(...)`.

### Hosted Auth settings (one-time)

Supabase dashboard → Authentication:

- **URL Configuration:** Site URL = the Vercel production URL; Redirect URLs =
  `https://<vercel-domain>/**` (plus `http://localhost:3000/**` if you test locally against prod).
- **Emails → Confirm signup:** paste `supabase/templates/confirmation.html` (links to
  `/auth/confirm`). "Confirm email" stays enabled (the default).
- **Render env:** `SUPABASE_URL=https://<project-ref>.supabase.co`, and `SUPABASE_JWT_SECRET`
  only while the project still signs with the legacy HS256 secret.
- **Vercel env:** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

## Consent (AD-11, Story 1.4)

- **Purposes (Phase 1):** `provider_runs` (runs under the Student's own Apify account, billed
  to it; LinkedIn's terms restrict scraping) and `pool_contribution` (Listings collected under
  the Student's key are shared with matching Students; without it they stay private). They are
  independent and optional during onboarding.
- **Records:** `app.consents` (owned by `accounts`): granting inserts a row with the text
  `version`, revoking sets `revoked_at`; history is kept, and a partial unique index allows one
  active row per owner and purpose. Rows cascade-delete with the account.
- **The one gate:** `accounts.service.require_consent(session, owner_id, purpose)` passes only
  with an active row on the purpose's **current** version; otherwise it raises
  `ConsentRequiredError` (403 `/problems/consent-required` with `purpose`). Never add another
  consent check or a consent flag elsewhere.
- **Revocation** publishes `core.events.ConsentRevoked(owner_id, purpose)` in the same
  transaction. Subscribers (`core.events.subscribe`) run before commit; if one raises, the
  revocation rolls back.
- **API:** `GET /api/v1/consents` (texts, status `granted|revoked|none|outdated`, DPDP rights
  notice), `PUT /api/v1/consents/{purpose}` with `{"version": ...}` (409
  `consent-version-mismatch` if the text changed), `DELETE /api/v1/consents/{purpose}` (204).
  The web screens are `/onboarding` (step 1) and `/settings/consent`.
- **Bumping a consent version:** edit the paragraphs in
  `api/app/modules/accounts/consent_text.py` and set that purpose's `version` to the date of the
  change (`YYYY-MM-DD`). Existing consents then show `outdated` and the gate blocks until the
  Student agrees again; no migration is needed. For the rights notice, bump
  `RIGHTS_NOTICE_VERSION`. A **new purpose** also needs a migration widening
  `consents_purpose_check`. Run `npm run gen:api` only if the API shape changed.

## Resumes and file storage (Story 1.5)

- **Storage:** every Student file lives in ONE private Supabase Storage bucket
  (`STORAGE_BUCKET`, default `private`) at `<owner_id>/<kind>/<uuid>`; the path is always
  chosen by the server. Only the api reaches it, through the `FileStore` port
  (`app/ports/file_store.py`), implemented by `app/adapters/supabase_storage/` (Storage REST
  API over `httpx`, service-role key `SUPABASE_SERVICE_ROLE_KEY`). Tests use the in-memory
  fake in `api/tests/fakes/file_store.py`.
- **Bucket:** locally `supabase/config.toml` declares `[storage.buckets.private]` (private, PDF/DOCX/plain-text MIME
  types only, 5 MiB limit). `npx supabase start` creates it on a fresh stack; on a stack that already
  existed, run `npx supabase seed buckets` or `uv run python scripts/ensure_bucket.py`. Hosted, `api/scripts/ensure_bucket.py`
  creates it if missing (idempotent, never public); `migrate-deploy.yml` runs it after the
  migrations, so add the GitHub `production` secrets `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY` (and optionally the variable `STORAGE_BUCKET`).
- **Rules:** PDF (`%PDF-`) or DOCX (a ZIP containing `word/document.xml`), sniffed from the
  content, up to 5 MB (413 / 415 otherwise, nothing stored). At most `RESUME_MAX_VERSIONS`
  (default 10) versions per Student (409 "Delete an old version first"). A partial unique
  index keeps one active version per Student; an upload becomes active only when none is.
  Activating publishes `core.events.ResumeActivated(owner_id, resume_id)` in the same
  transaction.
- **API:** `POST /api/v1/resumes` (multipart `file`), `GET /api/v1/resumes` (`limit`,
  `cursor`; `items`, `next_cursor`), `POST /api/v1/resumes/{id}/activate`,
  `GET /api/v1/resumes/{id}/download` (`{url, expires_at}`: a signed attachment URL valid for
  5 minutes; bytes never pass through the api) and `DELETE /api/v1/resumes/{id}` (the row goes
  now; the object is removed by the `profile.delete_stored_files` job, drained right after
  the response). Another Student's id is a 404.
- **Web:** onboarding step 2 (after consent) and `/settings/resumes`.

## Documents (Story 1.8)

- Other application files (cover letters, transcripts, certificates) on the same file path
  as Resumes: stored at `<owner_id>/document/<id>`, table `app.documents`. Accepted: PDF,
  DOCX or UTF-8 plain text, 5 MB or smaller. PDF and DOCX are sniffed as for Resumes; plain
  text (`text/plain`) is accepted only when it is non-empty,
  strict UTF-8 (a leading BOM allowed), has no NUL byte and no control characters other than
  tab, LF, CR and FF. Up to 5 MB (413 / 415 "Upload a PDF, DOCX or plain-text file"). At most
  `DOCUMENT_MAX_COUNT` (default 20) per Student (409 "Delete an old document first"). The
  stored name's extension always matches the sniffed type (`.pdf`; `.docx`; `.txt`, `.md`,
  `.csv` or `.log` for text), appended when missing (`cover.html` -> `cover.html.txt`).
- **API:** `POST /api/v1/documents` (multipart `file`), `GET /api/v1/documents` (`limit`,
  `cursor`), `GET /api/v1/documents/{id}/download` (`{url, expires_at}`) and
  `DELETE /api/v1/documents/{id}` (the object is removed by the
  `profile.delete_document_file` job). No events are published. Another Student's id is a 404.
- **Web:** `/settings/documents`.

## Account deletion (FR-2, AD-17, Story 1.9)

- **What is removed:** the `accounts` row and, by `ON DELETE CASCADE`, every Student-owned row
  (consents, Resumes, Skill Profile, Preferences, Documents, schedules, and any later
  `owner_id` table); every stored object under `<owner_id>/` in the private bucket; and the
  Supabase Auth user. Nothing is soft-deleted, there is no grace period or undo, and no email
  or other personal data is kept.
- **What survives:** global rows only. `ops_events` and `admin_audit` keep ids and counts, with
  their Student references set to NULL. `app.deleted_accounts` keeps a tombstone of the Auth
  uid and timestamps (no email), so an access token issued before deletion gets 401 instead of
  re-provisioning an empty account.
- **How:** `DELETE /api/v1/me` with `{"confirm_email": ...}` (must equal the account email,
  ignoring case and surrounding spaces; otherwise 422 on `confirm_email` and nothing changes).
  One transaction publishes `AccountDeleting`, writes the tombstone, records `account.deleted`,
  enqueues `accounts.purge_files` and `accounts.delete_auth_user` on the `priority` queue, and
  deletes the `accounts` row. The after-response priority drain runs both jobs within seconds.
- **Completion within 30 days:** both jobs are idempotent and retry for about 4 days (10
  attempts, exponential backoff). On success each one stamps its column on the tombstone
  (`files_purged_at`, `auth_deleted_at`) and records `account.files_purged`
  (`counts.objects`) or `account.auth_deleted`, with no owner id. A global daily schedule row
  (inserted by migration 0010) runs `accounts.resume_pending_deletions`, which re-enqueues the
  missing job(s) for every tombstone older than an hour that is not stamped, using the same
  keys, so a pending run is not duplicated. So a deletion keeps being retried daily until it
  finishes. It finishes within 30 days as long as Storage and Auth are reachable on some day
  in that window and the drains are running. A tombstone with a NULL column is a deletion
  that has not finished yet.
- **Convention:** `tests/test_owner_fk_convention.py` fails if a new `app` table with an
  `owner_id` column does not cascade from `accounts(id)` (the allow-list is `ops_events`, SET
  NULL), or if any FK to `accounts` is neither CASCADE nor SET NULL.
- **Web:** `/settings/account` (type your email to enable "Delete my account"); after a 204
  the browser session is cleared and the user lands on the public `/account-deleted` page.

## CI (`.github/workflows/ci.yml`)

On every push and PR: Postgres 17 service + Supabase roles SQL → `alembic upgrade head` →
pytest → `lint-imports` → exposed-schema check; web lint, typecheck and build; then
`npm run gen:api` and `git diff --exit-code web/openapi.json web/src/api` (fails if an API
change was not regenerated and committed).

## Hosting setup (not provisioned yet)

Releases deploy with `.github/workflows/migrate-deploy.yml` (on `release: published` or manual
dispatch): **migrate → deploy-api → deploy-web**. Deploy jobs `need` the migrate job, so a
failed migration stops the release. Nothing deploys on push.

1. **Supabase (Free)** — create a project in the region nearest Render. Copy the
   **Session pooler** connection string (port **5432**; never the transaction pooler on 6543
   or the IPv6 direct host). Leave Data API exposed schemas at `public, graphql_public`.
2. **Render (Free)** — create a Blueprint from `render.yaml` (one web service, `rootDir: api`,
   auto-deploy off). Set `DATABASE_URL` (session pooler URL) and `WEB_ORIGIN` (the Vercel
   production URL), `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and (if still used)
   `SUPABASE_JWT_SECRET`. Copy the
   service's **Deploy Hook** URL.
3. **Vercel (Hobby)** — import the project with `web/` as the working directory (the CLI
   deploys from `web/`, so leave *Root Directory* empty); Git auto-deploy is disabled by
   `web/vercel.json`. Set `NEXT_PUBLIC_API_URL` to the Render URL, and
   `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` (see "Auth and roles"). Get the org and project
   ids from `web/.vercel/project.json` after `npx vercel link`, and create a token.
4. **GitHub `production` environment secrets** — `MIGRATION_DATABASE_URL`,
   `RENDER_DEPLOY_HOOK_URL`, `RENDER_API_KEY`, `RENDER_SERVICE_ID` (the `srv-...` id),
   `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY` (for `ensure_bucket.py`), and for the health/backup drain
   `DRAIN_SECRET` and `API_BASE_URL` (see "Background jobs").
5. Publish a (non-prerelease) GitHub release, or run the workflow manually from `main`, to
   migrate, run the exposed-schema check on prod, ensure the private Storage bucket, deploy the migrated commit to Render, wait
   for it to be live, then deploy web.

Every configuration key and where it lives is listed in [`.env.example`](.env.example).
Secrets come only from env or GitHub secrets; nothing secret is committed.
