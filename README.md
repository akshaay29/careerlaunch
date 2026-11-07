# CareerLaunch

Monorepo for CareerLaunch: a FastAPI modular monolith (`api/`), a Next.js UI (`web/`) and
Supabase CLI config (`supabase/`). The architecture rules live in
`_bmad-output/planning-artifacts/architecture/.../ARCHITECTURE-SPINE.md`.

| Path | What |
| --- | --- |
| `api/` | FastAPI 0.162 on Python 3.14, async SQLAlchemy 2.1 + psycopg 3, Alembic. Managed with `uv`. |
| `api/app/core/` | Kernel: config, db (search_path hook), problem+json errors, JSON logging. |
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
| `uv run alembic revision -m "..."` | New migration. Call `migrations.helpers.create_app_table_rls(table)` for every new table. |

| Command (from `web/`) | Purpose |
| --- | --- |
| `npm run gen:api` | Export OpenAPI (`web/openapi.json`) and regenerate `web/src/api/`. **Commit both.** Never hand-edit `src/api/`. |
| `npm run lint` / `npm run typecheck` / `npm run build` | Same checks as CI |

### Running the tests against plain Postgres (what CI does)

```bash
docker run -d --name cl-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:17
docker exec -i cl-pg psql -U postgres < api/tests/sql/supabase_roles.sql
cd api
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres
uv run alembic upgrade head && uv run pytest
```

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
4. **GitHub secrets** — `MIGRATION_DATABASE_URL`, `RENDER_DEPLOY_HOOK_URL`, `VERCEL_TOKEN`,
   `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`.
5. Publish a GitHub release to migrate and deploy.

Every configuration key and where it lives is listed in [`.env.example`](.env.example).
Secrets come only from env or GitHub secrets; nothing secret is committed.
