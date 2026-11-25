"""go-live-check: verify everything a machine can check before opening CareerLaunch to real
Students, and print the manual steps that remain (Story 4.4, ``docs/go-live.md``).

Usage (from ``api/``)::

    DATABASE_URL="$MIGRATION_DATABASE_URL" API_BASE_URL=https://<api host> \\
        uv run python scripts/go_live_check.py [--min-pool 50] [--timeout 120]

Connect as the role in ``MIGRATION_DATABASE_URL``: it bypasses RLS and may ``SET ROLE``
``anon`` / ``authenticated`` for the read probe.

Read-only: the database connection is a read-only transaction and the only HTTP request is
``GET <API_BASE_URL>/api/v1/public/health``. Prints one ``PASS``, ``FAIL`` or ``MANUAL`` line
per check and exits 1 when any check FAILs (0 otherwise; MANUAL steps are the operator's).
Run it from an operator machine, never from CI against production. Secrets are never printed.

Automatic checks (each in its own savepoint: an error becomes a FAIL line with a short
reason, and the remaining checks still run):

* the database is at the Alembic head revision (first);
* the pool has at least ``--min-pool`` active pool Listings;
* the ``pg_cron`` drain job exists and succeeded in the last 30 minutes (MANUAL when the
  ``cron`` schema does not exist, e.g. plain Postgres), and pg_net's ``net._http_response``
  holds an HTTP 200 from that window (MANUAL when the table does not exist: the
  health-drain workflow covers the HTTP result);
* ``GET /api/v1/public/health`` returns 200;
* the exposed-schema check (``scripts/check_exposed_schema.py``) passes, including the
  ``anon`` / ``authenticated`` read probe of every ``app`` relation;
* the four daily Listing maintenance schedules and the retention schedule exist, are not
  paused, and none is more than 2 hours overdue;
* no outbox row is older than 15 minutes and no ``todo`` job is due more than 1 hour ago.
"""

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

from app.core.config import get_settings, to_libpq_url  # noqa: E402
from scripts.check_exposed_schema import find_violations  # noqa: E402

HEALTH_PATH = "/api/v1/public/health"
DRAIN_JOB_NAME = "careerlaunch-drain"  # migration 0003
DRAIN_FRESH_MINUTES = 30
DEFAULT_MIN_POOL = 50
# Global (owner_id NULL) daily schedules: migration 0015's four plus 0021's retention job.
REQUIRED_SCHEDULES = (
    "listings.expire",
    "listings.purge",
    "listings.evict",
    "listings.quota_check",
    "core.purge_history",
)
SCHEDULE_OVERDUE_HOURS = 2
OUTBOX_STALE_MINUTES = 15
TODO_STALE_HOURS = 1

Status = Literal["PASS", "FAIL", "MANUAL"]


@dataclass(frozen=True)
class Result:
    status: Status
    check: str
    detail: str

    def line(self) -> str:
        return f"{self.status:<6} {self.check}: {self.detail}"


# --------------------------------------------------------------------------- database checks


def _relation_exists(conn: psycopg.Connection, qualified: str) -> bool:
    (exists,) = conn.execute(  # type: ignore[misc]
        "SELECT to_regclass(%s) IS NOT NULL", (qualified,)
    ).fetchone()
    return bool(exists)


def head_revisions() -> set[str]:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def check_migrations(conn: psycopg.Connection, heads: set[str]) -> Result:
    current = (
        {r[0] for r in conn.execute("SELECT version_num FROM app.alembic_version")}
        if _relation_exists(conn, "app.alembic_version")
        else set()
    )
    if current == heads:
        return Result("PASS", "migrations", f"at head {', '.join(sorted(heads))}")
    return Result(
        "FAIL",
        "migrations",
        f"database at {', '.join(sorted(current)) or 'nothing'}, head is "
        f"{', '.join(sorted(heads))}: run the release workflow (migrate-deploy.yml)",
    )


def check_pool(conn: psycopg.Connection, min_pool: int) -> Result:
    (active,) = conn.execute(
        "SELECT count(*) FROM app.listings "
        "WHERE source <> 'manual' AND visibility_owner_id IS NULL AND expired_at IS NULL"
    ).fetchone()  # type: ignore[misc]
    status: Status = "PASS" if active >= min_pool else "FAIL"
    detail = f"{active} active pool Listings (need at least {min_pool})"
    if status == "FAIL":
        detail += "; run the seed (see the MANUAL seed step)"
    return Result(status, "pool size", detail)


def check_drain_cron(conn: psycopg.Connection) -> Result:
    name = "pg_cron drain"
    (has_cron,) = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'cron')"
    ).fetchone()  # type: ignore[misc]
    if not has_cron:
        return Result(
            "MANUAL",
            name,
            "no cron schema here (plain Postgres?): in the Supabase dashboard, Integrations > "
            f"Cron, check that job '{DRAIN_JOB_NAME}' runs every 5 minutes and succeeds",
        )
    row = conn.execute(
        "SELECT jobid FROM cron.job WHERE jobname = %s", (DRAIN_JOB_NAME,)
    ).fetchone()
    if row is None:
        return Result(
            "FAIL",
            name,
            f"cron job '{DRAIN_JOB_NAME}' does not exist (migration 0003 skips it when "
            "pg_cron, pg_net or Vault is missing; see README 'Background jobs')",
        )
    (recent,) = conn.execute(
        "SELECT max(end_time) FROM cron.job_run_details "
        "WHERE jobid = %s AND status = 'succeeded' "
        "AND end_time > now() - make_interval(mins => %s)",
        (row[0], DRAIN_FRESH_MINUTES),
    ).fetchone()  # type: ignore[misc]
    if recent is None:
        return Result(
            "FAIL",
            name,
            f"cron job '{DRAIN_JOB_NAME}' has no successful run in the last "
            f"{DRAIN_FRESH_MINUTES} minutes (see cron.job_run_details)",
        )
    return Result("PASS", name, f"last successful run at {recent.isoformat()}")


def check_drain_http(conn: psycopg.Connection) -> Result:
    """pg_cron only records that the HTTP request was queued; pg_net's
    ``net._http_response`` records whether the drain answered. A 200 in the window passes."""
    name = "drain HTTP result"
    if not _relation_exists(conn, "net._http_response"):
        return Result(
            "MANUAL",
            name,
            "no net._http_response here: the drain's HTTP result is covered by the "
            "health-drain workflow (see the 'drain health workflow' step)",
        )
    (recent,) = conn.execute(
        "SELECT max(created) FROM net._http_response "
        "WHERE status_code = 200 AND created > now() - make_interval(mins => %s)",
        (DRAIN_FRESH_MINUTES,),
    ).fetchone()  # type: ignore[misc]
    if recent is None:
        return Result(
            "FAIL",
            name,
            f"no HTTP 200 in net._http_response in the last {DRAIN_FRESH_MINUTES} minutes "
            "(see its status_code and error_msg, and the Render logs)",
        )
    return Result("PASS", name, f"last HTTP 200 at {recent.isoformat()}")


def check_exposed_schema(conn: psycopg.Connection) -> Result:
    violations = find_violations(conn)
    if not violations:
        return Result("PASS", "exposed schema", "nothing reachable with the anon key")
    shown = "; ".join(violations[:5])
    more = f" (and {len(violations) - 5} more)" if len(violations) > 5 else ""
    return Result("FAIL", "exposed schema", shown + more)


def check_schedules(conn: psycopg.Connection) -> Result:
    rows = {
        kind: (paused, overdue)
        for kind, paused, overdue in conn.execute(
            "SELECT kind, paused, next_run_at < now() - make_interval(hours => %s) "
            "FROM app.schedules WHERE owner_id IS NULL AND kind = ANY(%s)",
            (SCHEDULE_OVERDUE_HOURS, list(REQUIRED_SCHEDULES)),
        )
    }
    missing = [k for k in REQUIRED_SCHEDULES if k not in rows]
    paused = [k for k in REQUIRED_SCHEDULES if k in rows and rows[k][0]]
    overdue = [k for k in REQUIRED_SCHEDULES if k in rows and not rows[k][0] and rows[k][1]]
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if paused:
        problems.append(f"paused {', '.join(paused)}")
    if overdue:
        problems.append(
            f"overdue by more than {SCHEDULE_OVERDUE_HOURS} hours {', '.join(overdue)} "
            "(is the drain running?)"
        )
    if problems:
        return Result("FAIL", "daily schedules", "; ".join(problems))
    return Result(
        "PASS", "daily schedules", f"{len(REQUIRED_SCHEDULES)} present, active and on time"
    )


def check_job_backlog(conn: psycopg.Connection) -> Result:
    """Work that should have moved on: outbox rows older than 15 minutes (each drain
    flushes them) and ``todo`` jobs due more than an hour ago."""
    (outbox,) = conn.execute(
        "SELECT count(*) FROM app.job_outbox "
        "WHERE created_at < now() - make_interval(mins => %s)",
        (OUTBOX_STALE_MINUTES,),
    ).fetchone()  # type: ignore[misc]
    # A todo job is due at scheduled_at, else from when it was deferred (its first event).
    (todo,) = conn.execute(
        "SELECT count(*) FROM app.procrastinate_jobs j WHERE j.status = 'todo' "
        "AND coalesce(j.scheduled_at, (SELECT min(e.at) FROM app.procrastinate_events e "
        "WHERE e.job_id = j.id)) < now() - make_interval(hours => %s)",
        (TODO_STALE_HOURS,),
    ).fetchone()  # type: ignore[misc]
    problems = []
    if outbox:
        problems.append(f"{outbox} outbox rows older than {OUTBOX_STALE_MINUTES} minutes")
    if todo:
        problems.append(f"{todo} todo jobs due more than {TODO_STALE_HOURS} hour ago")
    if problems:
        return Result("FAIL", "job backlog", "; ".join(problems) + " (is the drain running?)")
    return Result("PASS", "job backlog", "no stale outbox rows or overdue jobs")


# --------------------------------------------------------------------------- HTTP check

HttpGet = Callable[[str, float], int]


def _http_get(url: str, timeout: float) -> int:
    return httpx.get(url, timeout=timeout, follow_redirects=False).status_code


def check_health(api_base_url: str | None, timeout: float, http_get: HttpGet = _http_get) -> Result:
    name = "API health"
    if not api_base_url:
        return Result("FAIL", name, "API_BASE_URL is not set")
    url = api_base_url.rstrip("/") + HEALTH_PATH
    try:
        status = http_get(url, timeout)
    except Exception as exc:  # InvalidURL, transport errors: anything the request raises
        return Result("FAIL", name, f"GET {HEALTH_PATH} failed: {type(exc).__name__}")
    if status == 200:
        return Result("PASS", name, f"GET {HEALTH_PATH} returned 200")
    return Result("FAIL", name, f"GET {HEALTH_PATH} returned {status}")


# --------------------------------------------------------------------------- manual steps

MANUAL_STEPS: tuple[tuple[str, str], ...] = (
    (
        "drain health workflow",
        "run `gh run list --workflow health-drain.yml --limit 5` and check the latest run "
        "is green (completed / success); if not, open it with `gh run view <id> --log`",
    ),
    (
        "master key offline copy",
        "confirm CREDENTIALS_MASTER_KEYS (Render env) has an offline copy in the owner's "
        "password manager; without it every stored Provider Key is lost",
    ),
    (
        "Render free hours",
        "in the Render dashboard (Billing > Free usage), check instance hours this month stay "
        "under 750 (the 5-minute drain uses about 744); there must be only one free service",
    ),
    (
        "backup",
        'take a backup: `pg_dump "$MIGRATION_DATABASE_URL" --schema=app --schema=auth '
        "--format=custom --no-owner --file careerlaunch-$(date +%Y%m%d).dump` and keep the "
        "file offline (restore notes in docs/go-live.md)",
    ),
    (
        "production secrets",
        "Render env has CREDENTIALS_MASTER_KEYS and SUPABASE_SERVICE_ROLE_KEY; the GitHub "
        "`production` environment has SUPABASE_SERVICE_ROLE_KEY (`gh secret list --env "
        "production`); SEED_APIFY_TOKEN is set only in your shell for the seed run",
    ),
    (
        "seed run",
        'from api/: `SEED_APIFY_TOKEN=... uv run python scripts/seed_pool.py --role "<role>" '
        '--location "<location>"`, then re-run this check until the pool size passes',
    ),
)


def manual_results() -> list[Result]:
    return [Result("MANUAL", check, detail) for check, detail in MANUAL_STEPS]


# --------------------------------------------------------------------------- runner


def guarded(conn: psycopg.Connection, name: str, check: Callable[[], Result]) -> Result:
    """Run one check in its own savepoint. A database error (a missing table, no privilege
    on ``cron``, ...) or any other exception becomes a FAIL line with a short reason; the
    savepoint is rolled back so the next check still runs."""
    try:
        with conn.transaction():
            return check()
    except psycopg.Error as exc:
        message = (exc.diag.message_primary or type(exc).__name__).splitlines()[0][:200]
        return Result("FAIL", name, f"check failed: {message} (SQLSTATE {exc.sqlstate})")
    except Exception as exc:
        return Result("FAIL", name, f"check failed: {type(exc).__name__}")


def run_checks(
    conn: psycopg.Connection,
    *,
    api_base_url: str | None,
    min_pool: int = DEFAULT_MIN_POOL,
    timeout: float = 120.0,
    http_get: HttpGet = _http_get,
) -> list[Result]:
    """Every automatic check (on ``conn``, which the caller keeps read-only), migrations
    first, then the manual steps."""
    checks: list[tuple[str, Callable[[], Result]]] = [
        ("migrations", lambda: check_migrations(conn, head_revisions())),
        ("pool size", lambda: check_pool(conn, min_pool)),
        ("pg_cron drain", lambda: check_drain_cron(conn)),
        ("drain HTTP result", lambda: check_drain_http(conn)),
        ("API health", lambda: check_health(api_base_url, timeout, http_get)),
        ("exposed schema", lambda: check_exposed_schema(conn)),
        ("daily schedules", lambda: check_schedules(conn)),
        ("job backlog", lambda: check_job_backlog(conn)),
    ]
    return [guarded(conn, name, check) for name, check in checks] + manual_results()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-pool", type=int, default=DEFAULT_MIN_POOL)
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout (seconds)")
    args = parser.parse_args(argv)

    url = to_libpq_url(get_settings().sqlalchemy_url)
    try:
        conn = psycopg.connect(url)
    except psycopg.OperationalError as exc:
        print(f"FAIL   database: cannot connect ({type(exc).__name__})")
        return 1
    with conn:
        conn.read_only = True
        try:
            results = run_checks(
                conn,
                api_base_url=os.environ.get("API_BASE_URL"),
                min_pool=args.min_pool,
                timeout=args.timeout,
            )
        finally:
            conn.rollback()
    for result in results:
        print(result.line())
    failed = sum(r.status == "FAIL" for r in results)
    print(f"\n{failed} FAIL, {sum(r.status == 'MANUAL' for r in results)} MANUAL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
