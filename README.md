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
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > web/.env.local

# 1. Supabase CLI stack (Postgres on :54322, Auth, Storage, Studio on :54323)
npx supabase start

# 2. api
cd api
uv sync
uv run alembic upgrade head          # creates + locks down schema `app`
uv run uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000/api/v1/health  => {"status":"ok"}

# 3. web (new terminal)
cd web
npm ci
npm run dev
# -> http://localhost:3000 shows "API health: ok"
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
   production URL). Copy the service's **Deploy Hook** URL.
3. **Vercel (Hobby)** — import the project with `web/` as the working directory (the CLI
   deploys from `web/`, so leave *Root Directory* empty); Git auto-deploy is disabled by
   `web/vercel.json`. Set `NEXT_PUBLIC_API_URL` to the Render URL. Get the org and project
   ids from `web/.vercel/project.json` after `npx vercel link`, and create a token.
4. **GitHub `production` environment secrets** — `MIGRATION_DATABASE_URL`,
   `RENDER_DEPLOY_HOOK_URL`, `RENDER_API_KEY`, `RENDER_SERVICE_ID` (the `srv-...` id),
   `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, and for the health/backup drain
   `DRAIN_SECRET` and `API_BASE_URL` (see "Background jobs").
5. Publish a (non-prerelease) GitHub release, or run the workflow manually from `main`, to
   migrate, run the exposed-schema check on prod, deploy the migrated commit to Render, wait
   for it to be live, then deploy web.

Every configuration key and where it lives is listed in [`.env.example`](.env.example).
Secrets come only from env or GitHub secrets; nothing secret is committed.
