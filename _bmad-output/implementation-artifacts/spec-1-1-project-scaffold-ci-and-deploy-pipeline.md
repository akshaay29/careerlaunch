---
title: 'Story 1.1: Project scaffold, CI and deploy pipeline'
type: 'chore'
created: '2026-11-07'
status: 'done'
baseline_commit: '2bcf8000e21d4374c66fdbbbbd5e44e12a39f167'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repo has no code. Every later story needs a runnable, deployable skeleton that already enforces the architecture rules (AD-1, AD-3, AD-13, AD-14, AD-20), so that new code lands in the right place and CI catches drift.

**Approach:** Create the `api/` (FastAPI) + `web/` (Next.js) + `supabase/` monorepo with exact version pins, a health endpoint, one problem+json error handler, a first Alembic migration that locks down schema `app`, a CI workflow running the four guardrails, and a release workflow that migrates and then deploys.

## Boundaries & Constraints

**Always:**
- Pin the versions in the spine's Stack table exactly. Python deps are managed with `uv` (`pyproject.toml` + `uv.lock`) and web deps with `npm` (`package-lock.json`, exact versions with no `^`/`~`). Use Tailwind CSS + shadcn/ui.
- Use the module set `accounts, profile, listings, matching, tracker` (the story's set; `outreach`/`admin` arrive in later epics). Each module has `router.py service.py repo.py models.py schemas.py tasks.py events.py`, which can be stubs.
- import-linter contracts:
  - module layers `tracker > matching > listings > profile > accounts`;
  - `app.core` imports nothing from `app.modules`, `app.ports` or `app.adapters`;
  - `app.ports` imports only `app.core`;
  - vendor SDKs (`supabase`, `apify_client`, `googleapiclient`, `groq`, `brevo`) are importable only from `app.adapters`.
- Every DB session runs `SET search_path TO app` on connect, through a SQLAlchemy connect event. Do not use a startup `options` parameter, because the Supavisor session pooler drops it.
- All errors go out as `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`; validation adds `errors: [{loc, msg}]`) from one handler module. `type` is a relative URI `/problems/<slug>`.
- `/internal/*` routes are excluded from the OpenAPI schema.
- Secrets come only from env or GitHub secrets. `.env.example` lists every key with its home: Render env, Vercel env, GitHub secrets, or Supabase Vault.

**Never:**
- Business tables, auth, the job queue, Procrastinate schema, the drain, `health-drain.yml` (all Story 1.2+).
- Supabase client or DB access from `web`. Hand edits under `web/src/api/`.
- Schema edits outside Alembic. Tables in `public`/`graphql_public`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Health | `GET /api/v1/health` | 200 `{"status":"ok"}` | N/A |
| Unknown route | `GET /api/v1/nope` | 404 problem+json, `type=/problems/not-found` | one handler |
| Validation | bad query/body on a test route | 422 problem+json with `errors[]` | one handler |
| Unhandled | route raises `RuntimeError` | 500 problem+json, no stack trace or secret in the body; error logged as JSON | one handler |
| Domain error | `core.errors.DomainError` subclass raised | its declared status and `type` | one handler |

**Decisions (human, 2026-11-07):**
- **Git/GitHub:** run `git init` at the project root and create a GitHub repo with `gh repo create`, private unless the human says otherwise. Work happens on that repo: commit the scaffold on a feature branch, push, and verify CI there. `.gitignore` excludes `.env*` (except `.env.example`), `node_modules`, `.venv` and build output. Before the first push, decide with the human whether `_bmad/`, `.claude/` and `_bmad-output/` are committed.
- **Hosting:** no Supabase, Render or Vercel prod accounts exist yet. Write the config only (`migrate-deploy.yml`, `render.yaml`, README setup). The release workflow is not triggered, and real deploys stay unverified until the secrets are added.
- **Spec size:** keep the full spec (about 2,400 tokens) as one story.

</frozen-after-approval>

## Code Map

The project is greenfield: only `_bmad/`, `.claude/` and `_bmad-output/` exist. Nothing to reuse; do not modify `_bmad*` or `.claude/`.
- `api/app/core/{config,db,errors,logging}.py` -- the Settings class, async engine/session (psycopg 3, search_path hook), the problem+json handlers, and JSON logging with a request id.
- `api/app/{ports,adapters}/__init__.py` -- empty packages, ready for later ports.
- `api/app/modules/<m>/*.py` -- stubs, with each `router.py` exporting `router = APIRouter()`.
- `api/app/main.py` -- app factory, CORS (Vercel origin + localhost from settings), routers under `/api/v1`, error handlers.
- `api/migrations/` -- Alembic env (async, `version_table_schema='app'`) and `0001_app_schema`.
- `api/scripts/{check_exposed_schema,export_openapi}.py` -- CI guardrails.
- `api/tests/` -- `conftest.py` (migrated test DB), `fakes/`, and tests for health, errors, migration and settings.
- `web/` -- Next.js App Router, Tailwind, shadcn (`components.json`, `button`), `openapi-ts.config.ts`, `src/api/` (generated), `src/lib/api-client.ts` (baseUrl from `NEXT_PUBLIC_API_URL`), and a home page showing API health.
- `supabase/config.toml` -- from `npx supabase init`, with API-exposed schemas `public, graphql_public` only.
- `.github/workflows/{ci,migrate-deploy}.yml`, `render.yaml`, `.env.example`, `README.md`, `.gitignore`, `.nvmrc` (26), `api/.python-version` (3.14).

## Tasks & Acceptance

**Execution:**
- [x] `api/pyproject.toml`, `uv.lock` -- pinned deps (fastapi, pydantic, uvicorn, sqlalchemy[asyncio], psycopg[binary], alembic, procrastinate, pydantic-settings) and dev deps (pytest, pytest-asyncio, httpx, import-linter), plus the import-linter contracts -- AD-3/AD-20 enforcement.
- [x] `api/app/core/*`, `api/app/main.py`, `api/app/modules/**`, `ports/`, `adapters/` -- the skeleton above -- the source tree from the spine.
- [x] `api/migrations/env.py`, `alembic.ini`, `versions/0001_app_schema.py` -- `CREATE SCHEMA app`; `REVOKE ALL ON SCHEMA app FROM public, anon, authenticated`; `ALTER DEFAULT PRIVILEGES IN SCHEMA app REVOKE ALL ON TABLES, SEQUENCES, FUNCTIONS FROM public, anon, authenticated`; provide `migrations/helpers.py::create_app_table_rls(table)` (enable + force RLS) for later migrations -- AD-1/AD-13.
- [x] `api/scripts/check_exposed_schema.py` -- exit 1 if any relation exists in `public`/`graphql_public`, if any `app` table lacks RLS, or if `anon`/`authenticated` hold any privilege on schema `app` or its objects -- the AD-1 CI check.
- [x] `api/tests/sql/supabase_roles.sql` -- create the `anon`/`authenticated` roles if missing (CI plain Postgres only).
- [x] `api/tests/*` -- I/O matrix cases, migration upgrade/downgrade, search_path is `app`, and the exposed-schema script passes.
- [x] `web/**` -- the scaffold above; `npm run gen:api` = export OpenAPI → `web/openapi.json` → `openapi-ts`; `web/vercel.json` turns off Git auto-deploy.
- [x] `.github/workflows/ci.yml` -- on push/PR: a postgres:17 service, roles SQL, `alembic upgrade head`, pytest, `lint-imports`, the exposed-schema check, the web lint/typecheck/build, then `npm run gen:api && git diff --exit-code web/openapi.json web/src/api`.
- [x] `.github/workflows/migrate-deploy.yml` -- on `release: published` + `workflow_dispatch`, in order: `migrate` (`alembic upgrade head` using secret `MIGRATION_DATABASE_URL`, session pooler :5432) → `deploy-api` (POST `RENDER_DEPLOY_HOOK_URL`) → `deploy-web` (`vercel deploy --prod` with `VERCEL_TOKEN/ORG_ID/PROJECT_ID`). Use `concurrency` so releases never overlap.
- [x] `render.yaml`, `.env.example`, `README.md` -- the Render service definition, every key with its home, and local setup (`npx supabase start`, `uv run`, `npm run dev`) plus hosting setup.

**Acceptance Criteria:**
- Given a fresh clone, when I follow the README, then the api serves `/api/v1/health` and `web` renders the health status, both against the Supabase CLI stack.
- Given the migrated DB, when the exposed-schema check runs, then it exits 0; and it exits 1 if a table is created in `public`.
- Given a module service that imports a module to its right, or a vendor SDK outside `adapters/`, when `lint-imports` runs, then it fails.
- Given an API route change without regenerating, when CI's client-diff step runs, then it fails.
- Given a release, when `migrate-deploy.yml` runs, then the deploy jobs `needs: migrate` and do not run if migrations fail.

## Design Notes

Node 26 is pinned in `.nvmrc`, `engines` and CI. Local Node 24 can still build, because `engine-strict` is not set. CI is the authority.

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass, run against `docker run postgres:17` with the roles SQL applied
- `cd api && uv run lint-imports` -- contracts kept; a temporary violating import makes it fail, then revert it
- `cd api && uv run python scripts/check_exposed_schema.py` -- exit 0
- `cd web && npm run lint && npx tsc --noEmit && npm run build` -- success
- `npm run gen:api` twice -- the second run produces no diff
- `actionlint` or a YAML parse of both workflows -- valid

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `conftest.py` loop-factory hook returns `None` off Windows | high | `pytest_asyncio/plugin.py:262` raises UsageError when a hook impl returns None, so CI pytest on ubuntu fails | patch |
| 2 | `tsc --noEmit` before Next typegen fails on a clean checkout | high | `LayoutProps` is declared only in generated `.next/types` (not in `next/dist/*.d.ts`), and `next-env.d.ts` and `.next` are gitignored | patch |
| 3 | 500 responses lack CORS and `x-request-id` headers | medium | The `Exception` handler runs in ServerErrorMiddleware, outside the CORS and RequestId middleware, so the browser sees a CORS failure | patch |
| 4 | pytest may migrate or downgrade `MIGRATION_DATABASE_URL` | high | `alembic_config()` never sets `attributes["database_url"]`, and `env.py` falls back to `migration_sqlalchemy_url`, which reads `.env` | patch |
| 5 | Exposed-schema check ignores functions in `public`/`graphql_public` | medium | It only queries `pg_class`; PostgREST serves `public` functions as RPC | patch |
| 6 | Exposed-schema check ignores column-level grants | low | `has_table_privilege` is not true for column-only grants; the fix is a direct addition to the same query loop | patch |
| 7 | Function check ignores the PUBLIC grantee | low | Unreachable without schema USAGE, which check 3a enforces; the code comment documents this | reject |
| 8 | Per-schema `ALTER DEFAULT PRIVILEGES ... REVOKE` is a no-op against the global defaults | low | True per the PG docs, but harmless and as the spec lists; protection comes from the missing USAGE | reject |
| 9 | Render hook deploys branch HEAD, not the migrated commit | medium | Deploy hook builds the tracked branch unless `ref` is passed | patch |
| 10 | `deploy-web` does not wait for the Render deploy to finish | medium | The hook POST returns immediately; nothing polls the deploy status | patch |
| 11 | `workflow_dispatch` or a prerelease can migrate prod from any ref; no environment protection | medium | No `if:` or `environment:` on the jobs | patch |
| 12 | Actions pinned by tag, not SHA | low | Common practice; SHAs can't be verified offline | reject |
| 13 | `--proxy-headers` without `--forwarded-allow-ips` | low | uvicorn trusts only 127.0.0.1 by default; one-flag fix | patch |
| 14 | Prod CORS allows localhost | false | The spine (AD-14) mandates the Vercel origin plus localhost; CORS is not auth (bearer JWT) | reject |
| 15 | `cn` package is not clsx + tailwind-merge | false | `node_modules/cn/package.json`: "Drop-in replacement for clsx + tailwind-merge" | reject |
| 16 | Generated client and lockfiles missing from diff | false | Excluded from the review diff only; committed in `ba51bef` | reject |
| 17 | API base URL is set only as an import side effect; an empty env var gives an empty baseUrl | medium | Any SDK caller not importing `@/lib/api-client` hits the web origin; `??` passes `""` through | patch |
| 18 | No lint rule bans raw `fetch` in web | low | Future guardrail, not a defect now | reject |
| 19 | import-linter allows modules→adapters and adapters→modules | low | Not covered by any contract; adding forbidden contracts is a direct config addition | patch |
| 20 | Alembic `include_schemas=True` without a filter | medium | Once metadata is set, autogenerate would diff the `auth`/`storage` schemas and emit drops | patch |
| 21 | `ENVIRONMENT`/`LOG_LEVEL` plain `str`; bad level crashes startup | low | Direct type change to `Literal` | patch |
| 22 | Incoming `x-request-id` not character-restricted | low | Headers come pre-parsed by h11 (no CR/LF), and logs are JSON-escaped | reject |
| 23 | Migration template lacks an RLS reminder | low | The CI check catches it | reject |
| 24 | 500 log request id never asserted | medium | Gap filed pre-verified | patch |
| 25 | Exposed-schema sequence, function and schema-CREATE checks lack failing tests | medium | Gap filed pre-verified | patch |
| 26 | No web test for the health component or base URL | medium | Gap filed pre-verified; there is no web test runner yet | defer |
| 27 | `../.env` overrides `api/.env`; paths are cwd-relative | low | pydantic-settings gives later files priority; anchoring is a direct fix | patch |
| 28 | Blank `DATABASE_URL` gives an opaque error | low | Fails loudly on bad config; a guard adds complexity | reject |
| 29 | Offline (`--sql`) mode misses `CREATE SCHEMA` | low | Mode not used anywhere; a fix adds a branch | reject |
| 30 | Health UI has no timeout on cold start | low | Request eventually resolves or errors; a fix adds complexity | reject |
| 31 | Release never runs the exposed-schema check against prod | medium | The migrate job runs only alembic | patch |
