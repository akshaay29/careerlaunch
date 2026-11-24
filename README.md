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
| `uv run python scripts/block_listing.py block\|unblock --listing SOURCE:ID \| --url URL \| --company NAME` | Owner takedown of a Listing, URL or company (audited; local/ci only unless `--yes-prod`; see "Expiry, eviction and takedown") |
| `uv run python scripts/seed_demo_listings.py` | Put ~30 fake demo Listings in the shared pool (local/ci only; idempotent; see "Listings") |
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

## Listings (FR-6, FR-7, AD-3, AD-7, Story 2.1)

- **Tables:** `app.sources` (global; `enabled` gates collection per Source; seeded with
  `linkedin` and `manual`) and `app.listings` (global, no `owner_id`, unique on
  `(source, source_job_id)`). A pool Listing keeps only title, company, location, job type,
  posted date, deadline, source URL, skills (with `vocab_version`) and `search_doc` (a
  weighted tsvector: title A, company B, skill labels C, description D). **No column holds the
  description**: it reaches Postgres only as a bind parameter of `to_tsvector`, then is gone.
- **Source seam:** `app/ports/source_adapter.py` defines `ListingDraft` (validated: stripped,
  length-capped text; `http(s)` URLs only), `SourceQuery` and the `SourceAdapter` protocol.
  The in-memory fake is `tests/fakes/source_adapter.py`.
- **One ingest pipeline:** `listings.service.ingest_draft` upserts one draft;
  `collect_and_ingest` runs an adapter through it (returns 0 without calling a disabled
  Source and records `listings.collect_skipped`), reads the collector's `pool_contribution`
  consent once, stores each draft in its own savepoint (a refused draft is skipped and
  counted as `rejected`), records `listings.ingested` (counts only) and publishes one
  `ListingsIngested` per call. Called directly, `ingest_draft` publishes its own event.
- **Visibility:** `visibility_owner_id` NULL means the shared pool. A Student without
  `pool_contribution` consent collects private Listings (only they see them). Visibility only
  widens: when a consenting Student collects a private Listing it becomes public (and they
  become `collected_by`); a pool Listing never becomes private. Only a contributing
  collector, or the Student a row is private to, refreshes its content (a thinner scrape
  keeps known values, and a re-seen Listing whose deadline is unknown or not past is no
  longer expired); anyone else only advances `last_seen_at`. Deleting a Student sets their
  pool rows' `collected_by` to NULL and deletes their private rows.
- **API:** `GET /api/v1/listings` (`limit` default 20, max 50; opaque `cursor`), newest
  first by `(first_seen_at, id)`, and `GET /api/v1/listings/{id}` (404 for anything not
  visible to the Student, expired, or unknown). Expired rows (`expired_at` set) are hidden.
- **Web:** `/listings` and `/listings/[id]`. Every card and the detail page show "via
  {Source}" and "CareerLaunch is not affiliated with LinkedIn or any job board."; "Apply"
  opens the source URL in a new tab (`noopener noreferrer nofollow`, http(s) only). Scraped
  text is rendered as plain text.
- **Local demo data:** with the Supabase CLI stack running and migrated, run
  `uv run python scripts/seed_demo_listings.py` from `api/`. It creates (or relabels) a
  `demo` Source labelled "Demo data" and ingests ~30 invented Listings (`source_job_id`
  `demo-001`...) under it into the pool as the System collector, through the real pipeline;
  running it again refreshes the same rows. It runs only with `ENVIRONMENT` `local` or `ci`.
  Then sign in and open `/listings`.

## Provider Key (FR-28, AD-6, AD-12, Story 2.2)

- **What it is:** each Student's own scoped Apify token. Collection (Story 2.3) runs under it,
  billed to the Student's Apify account (see Collection below). Settings page: `/settings/provider-key`.
- **API:** `GET /api/v1/provider-key` returns `{provider, has_key, last4, validated_at,
  needs_reentry}` (`needs_reentry`: the key was sealed with a master-key version that is no
  longer configured, so the Student must enter it again). `PUT` takes `{token}` (trimmed,
  then 8-200 printable ASCII characters with no whitespace, else 422 on `token`) and, in
  order: requires `provider_runs` consent (403 `consent-required`); validates the token with
  `GET /v2/users/me` at Apify with NO database transaction open (422 on `token` if rejected,
  503 `provider-unavailable` if Apify cannot answer); then, under the account lock, re-checks
  consent, encrypts and upserts it (a replacement keeps the row id) and publishes
  `ProviderKeyChanged`. `DELETE` removes it, pauses the owner's collection schedules
  (`accounts.COLLECTION_SCHEDULE_KINDS`, even when no key exists) and, if a key was removed,
  publishes `ProviderKeyChanged(removed=True)`; it is idempotent. Withdrawing
  `provider_runs` consent also pauses those schedules (the key is kept). Responses never
  contain the token or ciphertext.
- **Storage:** `app.provider_credentials` holds AES-256-GCM ciphertext from `core.crypto`
  (blob `v<version>:<nonce><ciphertext>`, version 1-32767, AAD
  `provider_credentials:<id>:<owner_id>`), the master-key version, `last4` and the Apify user
  id. Only `accounts.service.decrypt_credential` reads the plaintext, for the callers of the
  Apify adapter (Story 2.3); a blob that does not decrypt is a 409 `provider-key-unreadable`.
- **Logs:** the JSON formatter redacts `apify_api_...` and `Bearer <value>` anywhere (message,
  fields, traceback) and any `extra` field whose name contains `token`, `authorization`,
  `api_key`, `apikey`, `secret`, `password`, `master_key` or `ciphertext`.
- **`APIFY_API_BASE`** must be `https://` unless `ENVIRONMENT` is `local` or `ci`.

### Credential master key (`CREDENTIALS_MASTER_KEYS`)

- **Generate** one 32-byte key (version 1):
  `python -c "import os,base64;print('1:'+base64.b64encode(os.urandom(32)).decode())"`
- **Where it lives:** the Render env var `CREDENTIALS_MASTER_KEYS` (`sync: false`, set in the
  dashboard) plus an offline copy in the owner's password manager. Never in the repo, the
  database, GitHub or Vercel. Locally, put a separately generated key in `.env`. Losing every
  copy makes all stored Provider Keys unreadable (Students must add them again).
- **Unset:** the api still starts; saving (or decrypting) a key fails with 503
  `credentials-unavailable`. A malformed value fails startup (the error never shows the key).
- **Rotate:** generate a new key with the next version and set
  `CREDENTIALS_MASTER_KEYS=1:<old>,2:<new>`. New saves use the highest version; existing rows
  still decrypt with the version in their blob (`provider_credentials.key_version`). Drop the
  old version only after no row has `key_version` = 1 (a re-encryption job is not built yet;
  Students replacing their key, or a one-off script using `decrypt`/`encrypt`, moves rows).


## Collection (FR-6, AD-4, AD-12, Story 2.3)

- **Trigger and cadence:** saving a Provider Key (the `ProviderKeyChanged` subscriber in
  `listings.events`) creates or resumes the Student's `schedules` row (`kind`
  `listings.collect`, every `COLLECT_INTERVAL_HOURS` (at least 24), next run at
  `COLLECT_LOCAL_HOUR`:00 (default 6) in the Student's time zone; re-saving the key never
  pushes back an active schedule) and enqueues a
  first `listings.collect:student:<id>:<local_date>` job, so fresh Listings arrive within
  a few drains. After that `scheduler_tick` enqueues `listings.collect` once per interval.
  Deleting the key or withdrawing `provider_runs` consent pauses the schedule (Story 2.2).
- **One run per Student per local day:** `listings.collect` reads the Student's Preferences
  (no role titles: `listings.collect_skipped` with `no_preferences`, no run) and calls
  `listings.service.start_provider_run` with `run_key` `collect:<id>:<local_date>` (the day
  in the Student's time zone, `core.clock.local_day`). A second call for the same key never
  starts a second Apify run.
- **The one spend path:** `start_provider_run` re-checks `provider_runs` consent,
  `provider_status` (paused, or credit `exhausted_until` in the future), the kill switch
  `PROVIDER_RUNS_ENABLED`, `sources.enabled` for `linkedin` and that a key exists. A failure
  stores the `provider_runs` row as `refused` with an `error_kind`. Otherwise it commits the
  row as `starting`, then (no transaction open) decrypts the key and starts the first actor
  of `APIFY_ACTOR_ALLOWLIST` with `maxItems=COLLECT_MAX_ITEMS` (100) and
  `maxTotalChargeUsd=COLLECT_MAX_CHARGE_USD` (0.25), then stores the Apify run id
  (`running`) and enqueues `listings.poll_provider_run` 60 s later. A row left `starting`
  without a run id is another start in flight for 10 minutes (a no-op); after that it is a
  crash mid-start, becomes `failed`/`start_unknown` and is never restarted: better to miss a
  day than to pay twice. If the row was failed while our Apify call was in flight, the new
  run is aborted and recorded as `listings.provider_run_orphaned`. A start error is `failed` and is not
  retried; the next scheduled run collects again.
- **Polling:** the poll job never waits on Apify. While the run is READY/RUNNING it
  re-enqueues itself after 2, 4, 8, then every 10 minutes; after 3 hours the run is
  `failed`/`timeout` and aborted at Apify (best effort). On SUCCEEDED it fetches at most `max_items` items, maps them
  (`adapters/apify/linkedin_source.py`; bad items are counted as `rejected`) and ingests them
  through the Story 2.1 pipeline (`ingest_drafts`: pool or private per `pool_contribution`
  consent, descriptions discarded), then records the cost (`cost_usd`), the counts and
  `succeeded` plus a `listings.provider_run_succeeded` ops event. FAILED/ABORTED/TIMED-OUT is
  recorded as `failed` with its cost and never re-run. A transient Apify error raises, so the
  job's retry policy backs off. Counts satisfy `items = ingested + rejected + duplicates +
  not_visible`.
- **Reaper:** a global hourly `schedules` row (migration 0013) runs
  `listings.reap_stale_runs`: `starting` rows older than 15 minutes become
  `failed`/`start_unknown`, and `running` rows started more than 4 hours ago become
  `failed`/`poll_exhausted` after a best-effort abort.
- **Actors:** `curious_coder/linkedin-jobs-scraper` (primary, from about USD 1 per 1,000
  results) and `harvestapi/linkedin-job-search` (fallback; its optional `cookie` input is
  never set). Reorder `APIFY_ACTOR_ALLOWLIST` to switch. No cookie, login or session input is
  ever sent.
- **Costs:** every run is billed to the Student's own Apify account, capped at
  `COLLECT_MAX_CHARGE_USD` and `COLLECT_MAX_ITEMS` per run; at the defaults a daily run costs
  at most USD 0.25 (typically about USD 0.10 for 100 results), well inside Apify's USD 5 of
  free monthly credit.
- **Spend caps (Story 2.4):** each Student has a per-run cap (USD 0.05 to 2.00, default
  `COLLECT_MAX_CHARGE_USD`) and a monthly cap (USD 0.25 to 100 and at least the per-run cap,
  default `DEFAULT_MONTHLY_CAP_USD` = 4.00), set with `PUT /api/v1/spend-caps` or on
  `/settings/provider-key` (no `spend_caps` row means the defaults). A run's
  `max_charge_usd` is the per-run cap; its estimate is `max_items x APIFY_PRICE_PER_ITEM_USD`
  (default 0.001, so USD 0.10 for 100 items), stored as `estimated_cost_usd`. After the
  other checks, under a per-owner advisory lock, `start_provider_run` refuses with
  `run_cap` (estimate above the per-run cap), `monthly_cap` (this UTC month's committed
  spend plus the per-run cap above the monthly cap) or `platform_cap` (the same over every
  Student above the optional `PLATFORM_MONTHLY_CAP_USD`; unset means off). Committed spend
  is `cost_usd` of finished runs plus, for `starting`/`running` runs, their full
  `max_charge_usd` (worst case), so two quick runs can never together pass a cap. The
  System seed run skips the caps and is not counted in the platform total.
- **Pause on failure (Story 2.4):** when Apify refuses a Student run (on start or on a
  poll), the run is `failed` and `accounts.service.pause_provider` writes `provider_status`
  in the same transaction: an invalid token is `key_invalid`; a 402 or a usage/credit error
  type is `credit_exhausted` with `exhausted_until` = the end of the Apify billing cycle
  (`GET /v2/users/me/limits`, `monthlyUsageCycle.endAt`; the next UTC month when unknown);
  403 `insufficient-permissions` is `not_permitted`; any other 403 is `account_blocked`.
  Schedules are never paused for this: the daily tick keeps enqueuing and every start while
  paused is a cheap `refused` row (`paused` or `exhausted`) with no Apify call. Exhausted
  credit resumes by itself once `exhausted_until` has passed; saving a key (`PUT
  /api/v1/provider-key`) clears the pause. Seed runs never pause.
- **Usage:** `GET /api/v1/provider-usage` returns the Student's status (with the reason
  and resume date), caps, this month's spent and committed USD and the latest 30 runs
  (time, status, `error_kind`, estimate, actual cost, items, ingested; never run ids,
  search parameters or tokens). The Provider Key settings page shows it below the key.
- **Kill switch:** set `PROVIDER_RUNS_ENABLED=false` in Render to refuse every new run
  (rows are stored as `refused`/`disabled`); runs already started still finish and are
  ingested. Disabling the `linkedin` Source (`app.sources.enabled`) refuses runs too.
- **Seeding the pool before launch:** with the owner's own Apify token in
  `SEED_APIFY_TOKEN` (never in Render), run from `api/`:
  `uv run python scripts/seed_pool.py --role "Backend Engineering Intern" --location
  "Bengaluru, Karnataka, India" --max-items 50` (repeat `--role`/`--location`, optional
  `--job-type internship|full_time`, `--max-charge-usd` up to 2). It refuses to run without the token (exit code 2),
  starts one run through the same spend path as the System (`owner_id` NULL,
  `run_key` `seed:<uuid>`, every Listing goes to the pool), polls it from the script and
  prints the counts and the cost.

## Expiry, eviction and takedown (FR-8, NFR-7, AD-7, Story 2.5)

Four global daily jobs (`schedules` rows from migration `0015_listing_maintenance`, staggered
by 5 minutes from about 03:00 UTC) keep the shared pool fresh and inside the free database.
Each is idempotent, batched, writes a counts-only `ops_events` row and is retried by the jobs
layer if it raises.

| Job | What it does |
| --- | --- |
| `listings.expire` | Marks a Listing expired (`expired_at = now()`; browse and detail hide it) when its `deadline` is before today, or when it is a `linkedin` Listing last seen more than `LISTING_UNSEEN_DAYS` (14, at least 7) days ago **and** at least `LISTING_UNSEEN_RUNS` (3) succeeded provider runs have finished since. With too few completed runs nothing expires by unseen, so an idle pool never empties itself. `manual` Listings never expire by unseen. |
| `listings.purge` | Deletes Listings expired longer than `LISTING_PURGE_GRACE_DAYS` (30) ago. Never a pinned Listing (`pin_count > 0`). |
| `listings.evict` | When active (not expired) Listings exceed `LISTING_POOL_CAP` (30000), deletes the excess: lowest `relevance`, then oldest `last_seen_at`. Only unpinned rows, taken with `FOR UPDATE SKIP LOCKED`; a pinned Listing is never removed, even when the cap cannot be reached. |
| `listings.quota_check` | Reads `pg_database_size`; at or above `DB_QUOTA_ALERT_PERCENT` (80) percent of `DB_QUOTA_BYTES` (500 MiB) it records a `listings.quota_alert` ops event (`used_mb`, `quota_mb`, `percent`) and logs a warning. Watch `ops_events` for it. |

Every foreign key into `app.listings` must be `ON DELETE CASCADE` or `SET NULL` (a convention
test, `test_listing_fk_convention.py`, fails otherwise), because these jobs and takedowns
delete Listings.

**Hooks for later epics** (`listings.service`; they take a session and never commit):
`pin(session, listing_id)` (also clears `expired_at`), `unpin` (never below 0) and
`set_relevance(session, listing_id, relevance)` (finite, 0..100). Each locks the row, so it is
safe against eviction and purge; an unknown id raises `NotFoundError`.

**Takedown (owner CLI only; there is no public API or UI).** From `api/`:

```bash
uv run python scripts/block_listing.py block   --listing linkedin:3692563201
uv run python scripts/block_listing.py block   --url "https://www.linkedin.com/jobs/view/1?trk=x"
uv run python scripts/block_listing.py block   --company "Acme Corp"
uv run python scripts/block_listing.py unblock --company "Acme Corp"
```

`block` records the takedown in `listing_blocks`, deletes every matching Listing now (pinned
ones too: a takedown wins) and writes an `admin_audit` row (`listings.block`, `actor_id` NULL
= the CLI) and a counts-only ops event. A URL matches on its canonical form (lower-cased host,
no query, fragment or trailing slash); a company on its trimmed, case-folded name. A matching
draft is then never ingested again: it counts as `rejected`, is not an error and is not stored.
`unblock` lifts it (audited as `listings.unblock`; deleted Listings are not restored; exit
code 1 when there was no such takedown). The script runs only with `ENVIRONMENT` local or ci
unless you pass `--yes-prod`.

## Search (FR-13, NFR-2, Story 2.6)

`GET /api/v1/listings/search?q=&limit=&offset=` (`searchListings`) searches the Listings a
Student can see (the pool plus their own private ones, never expired or taken-down ones) and
returns `ListingOut` items, best match first, with `next_offset` (null when there are no
more). `limit` is 1 to 50 (default 20); `offset` is 0 to 200 (search shows the best matches,
not the whole pool). `/listings?q=...` runs the same search in the web app.

- The query is Unicode-normalised, lower-cased and cut into word tokens (letters and digits;
  at most 8 tokens of 40 characters). An empty query, one with fewer than 2 usable characters,
  or one over 100 characters is a 422. Only sanitised tokens reach the prefix `to_tsquery`
  (`a:* & b:*`), as a bind parameter; the application does not log or store the query text (only its length); the URL still carries it, so access logs and browser history can.
- A Listing matches when its `search_doc` (title, company, skills, description lexemes)
  matches every token as a word prefix, **or** the `pg_trgm` `word_similarity` of the query to
  `lower(title || ' ' || company)` (the stored column `listings.title_company`, GIN trigram
  index) reaches `SEARCH_TRIGRAM_THRESHOLD` (default 0.35, 0.1 to 0.9), so "bakend intrn"
  finds "Backend Intern". Full-text matches rank above similarity-only ones, by `ts_rank_cd`
  plus similarity; ties break by `first_seen_at DESC, id DESC`.
- Migration `0016_listing_search` enables `pg_trgm` in the `extensions` schema when it exists
  (Supabase) and in `public` otherwise (plain Postgres); the queries look the schema up at run
  time. `api/tests/test_listing_search_perf.py` seeds about 30,000 Listings and asserts typical
  queries finish in under 500 ms.

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
