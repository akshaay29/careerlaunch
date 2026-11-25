# Go-live checklist

Work through this before opening CareerLaunch to real Students, and again after any change to
hosting, secrets or schedules. Most of it is checked by a script; the rest are manual steps
with exact instructions.

## 1. Run the automatic check

From `api/`, on an operator machine (never from CI against production):

```bash
DATABASE_URL="$MIGRATION_DATABASE_URL" \
API_BASE_URL='https://<your-render-service>.onrender.com' \
  uv run python scripts/go_live_check.py [--min-pool 50] [--timeout 120]
```

Use `MIGRATION_DATABASE_URL` (the session pooler URL of the `postgres` role that runs the
migrations), not a restricted runtime role: the role must bypass RLS and be allowed to
`SET ROLE anon` / `SET ROLE authenticated` for the read probe. Otherwise the exposed-schema
check reports that it cannot probe.

The script is read-only: it opens a read-only database transaction and sends one
`GET /api/v1/public/health`. It prints one line per check, `PASS`, `FAIL` or `MANUAL`, and
exits 1 when anything FAILs. Each check runs on its own: a database error (a missing table,
no privilege on `cron`) or a bad `API_BASE_URL` is a FAIL line with a short reason, and the
other checks still run. Fix every FAIL and re-run until it exits 0. Secrets are never
printed; keep the URLs in your shell, not in a file in the repo.

| Check | PASS when | If it FAILs |
| --- | --- | --- |
| migrations (runs first) | `app.alembic_version` is the Alembic head | run the release workflow (`migrate-deploy.yml`) |
| pool size | at least `--min-pool` (50) active pool Listings (not manual, not private, not expired) | run the seed (step 2.6) |
| pg_cron drain | job `careerlaunch-drain` exists and succeeded in the last 30 minutes (`cron.job_run_details`). MANUAL when the database has no `cron` schema | README "Operator setup: drain secret": Vault secrets, then re-run the migrations |
| drain HTTP result | pg_net's `net._http_response` has an HTTP 200 from the last 30 minutes. MANUAL when that table does not exist (the health-drain workflow covers it) | its `status_code` / `error_msg`, then the Render logs |
| API health | `GET /api/v1/public/health` returns 200 | Render dashboard: service logs, env vars, last deploy |
| exposed schema | `scripts/check_exposed_schema.py` finds nothing, including a real read as `anon` and `authenticated` of every `app` table, view, materialized or foreign table (each must be refused with a permission error) | the violation names the object; fix it with a migration |
| daily schedules | the four Listing maintenance rows (`listings.expire`, `listings.purge`, `listings.evict`, `listings.quota_check`) and the retention row (`core.purge_history`) exist, are not paused and none is more than 2 hours overdue | re-run the migrations; unpause with `UPDATE app.schedules SET paused = false WHERE kind = '<kind>' AND owner_id IS NULL`; overdue means the drain is not running |
| job backlog | no `job_outbox` row older than 15 minutes and no `todo` job due more than 1 hour ago | the drain is not running or failing: check the pg_cron job, the health-drain workflow and the Render logs |

## 2. Manual steps

The script prints these as MANUAL lines too.

1. **Drain health workflow is green.**
   `gh run list --workflow health-drain.yml --limit 5`; the latest run must be
   `completed / success`. If not, `gh run view <run-id> --log` shows the HTTP status of
   `/internal/health` and of the backup drain.
2. **The credentials master key has an offline copy.** `CREDENTIALS_MASTER_KEYS` (Render env)
   must also be in the owner's password manager. Without it every stored Provider Key is
   unreadable (README "Credential master key").
3. **Render free instance hours stay within the limit.** Render dashboard, Billing, Free
   usage: this month's instance hours must stay under 750. The 5-minute drain keeps the one
   service awake (about 744 hours a month), so there must be no second free service.
4. **A backup was taken.** Supabase Free has no automatic backups:

   ```bash
   pg_dump "$MIGRATION_DATABASE_URL" --schema=app --schema=auth --format=custom --no-owner \
     --file careerlaunch-$(date +%Y%m%d).dump
   ```

   `auth` holds the sign-in users that `app.accounts` points at. Keep the file offline (it
   holds Student data).

   **Restoring.**
   - `app` goes into a fresh database (or a fresh Supabase project before any migration):
     `pg_restore --no-owner --schema=app --dbname "<target URL>" careerlaunch-<date>.dump`.
     Then run `uv run python scripts/check_exposed_schema.py` against it.
   - `auth` belongs to Supabase: never recreate its tables. Restore its rows only, into an
     existing Supabase project whose `auth` schema is already set up:
     `pg_restore --no-owner --data-only --schema=auth --dbname "<target URL>" careerlaunch-<date>.dump`.
     Restore `auth` before `app`'s data if both go into a project that already has the
     schema, so `app.accounts` finds its users.
5. **Production secrets are set.**
   - Render env: `CREDENTIALS_MASTER_KEYS` and `SUPABASE_SERVICE_ROLE_KEY` (plus the rest of
     README "Hosting setup").
   - GitHub `production` environment: `SUPABASE_SERVICE_ROLE_KEY`
     (`gh secret list --env production` lists the names, never the values).
   - `SEED_APIFY_TOKEN` only in your shell for the seed run; never in Render or the repo.
6. **Seed the pool.** From `api/`:

   ```bash
   SEED_APIFY_TOKEN=... uv run python scripts/seed_pool.py \
     --role "Backend Engineering Intern" --location "Bengaluru, Karnataka, India"
   ```

   Repeat with other roles or locations until the pool-size check passes.

## 3. What keeps running after launch

- **Retention** (`core.purge_history`, daily at about 03:30 UTC): deletes finished background
  jobs and leaked outbox rows after `JOB_HISTORY_RETENTION_DAYS` (7) and ops events after
  `OPS_EVENTS_RETENTION_DAYS` (90). It never deletes unfinished jobs, `admin_audit` or Student
  data, and records a counts-only `core.purge_history` ops event each run.
- **Listing maintenance** (03:00 to 03:15 UTC): expiry, purge, eviction to the pool cap and the
  database quota alert (README "Expiry, eviction and takedown").
- **Drain health** (`health-drain.yml`, every 15 minutes): a failed run emails the owner.
- **Manual Listings**: each Student may hold at most `MANUAL_LISTINGS_MAX` (200) jobs added
  from other sites.

Isolation, the anon-key lockout and the performance budgets are enforced in CI on every push:
`tests/test_isolation.py` (every id route, with a completeness guard), `tests/test_db_roles.py`
and the exposed-schema check, and the search (500 ms) and feed (2 s) budgets, strict when
`CI=true`.
