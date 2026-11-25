"""Story 4.4: ``scripts/go_live_check.py`` against the test database and a fake health
endpoint (a local HTTP server). It never runs against production from CI."""

import http.server
import importlib
import threading
from collections.abc import Iterator

import httpx
import psycopg
import pytest

from scripts import go_live_check as glc
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")


class _Health(http.server.BaseHTTPRequestHandler):
    status = 200
    paths: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        type(self).paths.append(self.path)
        code = type(self).status if self.path == glc.HEALTH_PATH else 404
        body = b'{"status":"ok"}' if code == 200 else b"{}"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def health_server() -> Iterator[tuple[str, type[_Health]]]:
    handler = type("Health", (_Health,), {"status": 200, "paths": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        server.server_close()


def _ready(db: MaintenanceDb, *, pool: int = 50) -> None:
    """A seeded pool and the five global daily schedules (as the migrations write them)."""
    db.execute(
        "INSERT INTO app.listings (id, source, source_job_id, url, title, company, "
        "vocab_version, search_doc) "
        "SELECT gen_random_uuid(), 'linkedin', 'golive-' || g, "
        "'https://www.linkedin.com/jobs/view/golive-' || g, 'Backend Intern', 'Acme Corp', 'v1', "
        "to_tsvector('english', 'x') FROM generate_series(1, %s) AS g",
        (pool,),
    )
    maintenance = importlib.import_module("migrations.versions.0015_listing_maintenance")
    retention = importlib.import_module("migrations.versions.0021_retention")
    rows = [(sid, task) for task, sid, _ in maintenance.MAINTENANCE_SCHEDULES]
    rows.append((retention.RETENTION_SCHEDULE_ID, retention.RETENTION_TASK))
    for schedule_id, task in rows:
        db.execute(
            'INSERT INTO app.schedules (id, kind, owner_id, next_run_at, "interval") '
            "VALUES (%s, %s, NULL, now() + interval '1 hour', interval '1 day')",
            (schedule_id, task),
        )


def _by_check(results: list[glc.Result]) -> dict[str, glc.Result]:
    return {r.check: r for r in results}


def test_the_required_schedules_are_the_migrations_tasks() -> None:
    maintenance = importlib.import_module("migrations.versions.0015_listing_maintenance")
    retention = importlib.import_module("migrations.versions.0021_retention")
    expected = [t for t, _, _ in maintenance.MAINTENANCE_SCHEDULES] + [retention.RETENTION_TASK]
    assert list(glc.REQUIRED_SCHEDULES) == expected


def test_a_ready_stack_passes_and_lists_the_manual_steps(
    mdb: MaintenanceDb,  # noqa: F811
    migrated_db: str,
    health_server: tuple[str, type[_Health]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, handler = health_server
    _ready(mdb)
    monkeypatch.setenv("API_BASE_URL", base + "/")
    assert glc.main([]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    passing = ("pool size", "API health", "migrations", "exposed schema", "daily schedules",
               "job backlog")
    for check in passing:
        assert any(line.startswith("PASS") and f" {check}:" in line for line in lines), check
    # The migrations check runs first.
    assert " migrations:" in lines[0]
    # Plain Postgres has no cron schema or pg_net: the drain checks are manual steps there.
    assert any(line.startswith("MANUAL") and " pg_cron drain:" in line for line in lines)
    http = next(line for line in lines if " drain HTTP result:" in line)
    assert http.startswith("MANUAL") and "health-drain workflow" in http
    for check, _ in glc.MANUAL_STEPS:
        assert any(line.startswith("MANUAL") and f" {check}:" in line for line in lines), check
    assert "gh run list --workflow health-drain.yml" in out
    assert "pg_dump" in out and "scripts/seed_pool.py" in out
    assert "CREDENTIALS_MASTER_KEYS" in out and "SUPABASE_SERVICE_ROLE_KEY" in out
    assert "SEED_APIFY_TOKEN" in out
    assert not any(line.startswith("FAIL") for line in lines)
    assert handler.paths == [glc.HEALTH_PATH]


def test_a_missing_schedule_or_a_failing_health_check_fails(
    mdb: MaintenanceDb,  # noqa: F811
    health_server: tuple[str, type[_Health]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, handler = health_server
    _ready(mdb)
    mdb.execute("DELETE FROM app.schedules WHERE kind = 'core.purge_history'")
    mdb.execute("UPDATE app.schedules SET paused = true WHERE kind = 'listings.evict'")
    handler.status = 503
    monkeypatch.setenv("API_BASE_URL", base)
    assert glc.main([]) == 1
    lines = capsys.readouterr().out.splitlines()
    schedules = next(line for line in lines if " daily schedules:" in line)
    assert schedules.startswith("FAIL")
    assert "missing core.purge_history" in schedules and "paused listings.evict" in schedules
    health = next(line for line in lines if " API health:" in line)
    assert health.startswith("FAIL") and "503" in health


def test_each_check_fails_on_its_own(
    sync_conn: psycopg.Connection,
    mdb: MaintenanceDb,  # noqa: F811
) -> None:
    _ready(mdb, pool=10)
    results = _by_check(
        glc.run_checks(sync_conn, api_base_url=None, http_get=lambda url, timeout: 200)
    )
    assert results["pool size"].status == "FAIL" and "10 active" in results["pool size"].detail
    assert results["API health"].status == "FAIL"
    assert "API_BASE_URL is not set" in results["API health"].detail
    assert glc.check_pool(sync_conn, 10).status == "PASS"

    # Manual, private and expired Listings are not pool Listings.
    owner = mdb.execute("INSERT INTO app.accounts (id, email) VALUES "
                        "('00000000-0000-0000-0000-00000000a001', 'golive-owner@example.test') "
                        "RETURNING id")[0][0]
    try:
        mdb.add("golive-manual", source="manual", visibility_owner_id=owner)
        mdb.add("golive-private", visibility_owner_id=owner)
        assert "10 active" in glc.check_pool(sync_conn, 50).detail
        mdb.execute("UPDATE app.listings SET expired_at = now() WHERE source_job_id = 'golive-1'")
        assert "9 active" in glc.check_pool(sync_conn, 50).detail
    finally:
        mdb.execute("DELETE FROM app.listings WHERE visibility_owner_id = %s", (owner,))
        mdb.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))

    behind = glc.check_migrations(sync_conn, {"9999_not_yet"})
    assert behind.status == "FAIL" and "9999_not_yet" in behind.detail
    assert glc.check_migrations(sync_conn, glc.head_revisions()).status == "PASS"

    sync_conn.execute("CREATE TABLE public.cl_go_live_leak (id int)")
    exposed = glc.check_exposed_schema(sync_conn)
    assert exposed.status == "FAIL" and "public.cl_go_live_leak" in exposed.detail


def test_health_reports_an_unreachable_api() -> None:
    def unreachable(url: str, timeout: float) -> int:
        raise httpx.ConnectError("connection refused")

    result = glc.check_health("https://api.example.test", timeout=2, http_get=unreachable)
    assert result.status == "FAIL" and "ConnectError" in result.detail


def test_the_cron_check_reads_the_drain_runs(sync_conn: psycopg.Connection) -> None:
    """On plain Postgres there is no ``cron`` schema; a stand-in with pg_cron's columns
    (rolled back after the test) exercises the PASS and FAIL paths."""
    assert glc.check_drain_cron(sync_conn).status == "MANUAL"
    sync_conn.execute("CREATE SCHEMA cron")
    sync_conn.execute("CREATE TABLE cron.job (jobid bigint, jobname text)")
    sync_conn.execute(
        "CREATE TABLE cron.job_run_details (jobid bigint, status text, end_time timestamptz)"
    )
    missing = glc.check_drain_cron(sync_conn)
    assert missing.status == "FAIL" and "does not exist" in missing.detail
    sync_conn.execute("INSERT INTO cron.job VALUES (7, %s)", (glc.DRAIN_JOB_NAME,))
    sync_conn.execute(
        "INSERT INTO cron.job_run_details VALUES (7, 'succeeded', now() - interval '2 hours'), "
        "(7, 'failed', now() - interval '1 minute')"
    )
    stale = glc.check_drain_cron(sync_conn)
    assert stale.status == "FAIL" and "no successful run" in stale.detail
    sync_conn.execute(
        "INSERT INTO cron.job_run_details VALUES (7, 'succeeded', now() - interval '4 minutes')"
    )
    assert glc.check_drain_cron(sync_conn).status == "PASS"


def test_the_database_session_is_read_only(
    mdb: MaintenanceDb,  # noqa: F811
    health_server: tuple[str, type[_Health]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` opens a read-only transaction: a write slipped into a check would fail."""
    base, _ = health_server
    _ready(mdb)
    monkeypatch.setenv("API_BASE_URL", base)
    seen: list[bool] = []
    original = glc.check_pool

    def spying(conn: psycopg.Connection, min_pool: int) -> glc.Result:
        (setting,) = conn.execute("SHOW transaction_read_only").fetchone()  # type: ignore[misc]
        seen.append(setting == "on")
        return original(conn, min_pool)

    monkeypatch.setattr(glc, "check_pool", spying)
    assert glc.main([]) == 0
    assert seen == [True]


# --------------------------------------------------------------------------- review follow-ups


def test_an_overdue_schedule_fails(mdb: MaintenanceDb, sync_conn: psycopg.Connection) -> None:  # noqa: F811
    _ready(mdb)
    assert glc.check_schedules(sync_conn).status == "PASS"
    mdb.execute(
        "UPDATE app.schedules SET next_run_at = now() - interval '3 hours' "
        "WHERE kind = 'listings.purge'"
    )
    result = glc.check_schedules(sync_conn)
    assert result.status == "FAIL" and "overdue" in result.detail
    assert "listings.purge" in result.detail and "listings.expire" not in result.detail
    # Just under the limit is still on time.
    mdb.execute(
        "UPDATE app.schedules SET next_run_at = now() - interval '1 hour' "
        "WHERE kind = 'listings.purge'"
    )
    assert glc.check_schedules(sync_conn).status == "PASS"


def test_a_stale_outbox_or_an_overdue_todo_job_fails(sync_conn: psycopg.Connection) -> None:
    assert glc.check_job_backlog(sync_conn).status == "PASS"
    sync_conn.execute(
        "INSERT INTO app.job_outbox (id, task, key, created_at) VALUES "
        "('00000000-0000-0000-0000-00000000b001', 'tests.go_live', 'tests.go_live:x:1', "
        "now() - interval '5 minutes')"
    )
    assert glc.check_job_backlog(sync_conn).status == "PASS"  # a fresh row is fine
    sync_conn.execute(
        "UPDATE app.job_outbox SET created_at = now() - interval '20 minutes' "
        "WHERE id = '00000000-0000-0000-0000-00000000b001'"
    )
    stale = glc.check_job_backlog(sync_conn)
    assert stale.status == "FAIL" and "1 outbox rows older than 15 minutes" in stale.detail

    sync_conn.execute("DELETE FROM app.job_outbox")
    sync_conn.execute("SET LOCAL search_path TO app")  # Procrastinate's trigger SQL
    sync_conn.execute(
        "INSERT INTO app.procrastinate_jobs (queue_name, task_name, status, args, scheduled_at) "
        "VALUES ('default', 'tests.go_live', 'todo', '{}', now() - interval '2 hours')"
    )
    todo = glc.check_job_backlog(sync_conn)
    assert todo.status == "FAIL" and "1 todo jobs" in todo.detail


def test_the_drain_http_result_is_read_from_pg_net(sync_conn: psycopg.Connection) -> None:
    """A stand-in for pg_net's table (rolled back after the test)."""
    assert glc.check_drain_http(sync_conn).status == "MANUAL"
    sync_conn.execute("CREATE SCHEMA net")
    sync_conn.execute("CREATE TABLE net._http_response (status_code int, created timestamptz)")
    sync_conn.execute(
        "INSERT INTO net._http_response VALUES (503, now() - interval '1 minute'), "
        "(200, now() - interval '2 hours')"
    )
    failing = glc.check_drain_http(sync_conn)
    assert failing.status == "FAIL" and "no HTTP 200" in failing.detail
    sync_conn.execute("INSERT INTO net._http_response VALUES (200, now() - interval '3 minutes')")
    assert glc.check_drain_http(sync_conn).status == "PASS"


def test_a_database_error_is_a_fail_line_and_later_checks_still_run(
    sync_conn: psycopg.Connection,
) -> None:
    sync_conn.execute("ALTER TABLE app.job_outbox RENAME TO job_outbox_hidden")
    results = glc.run_checks(sync_conn, api_base_url="http://x.test", http_get=lambda u, t: 200)
    by = _by_check(results)
    assert by["job backlog"].status == "FAIL"
    assert "does not exist" in by["job backlog"].detail and "42P01" in by["job backlog"].detail
    assert by["API health"].status == "PASS"  # earlier and later checks were not affected
    assert [r.check for r in results][0] == "migrations"
    assert by["migrations"].status == "PASS"


def test_missing_cron_privileges_are_a_fail_line(sync_conn: psycopg.Connection) -> None:
    sync_conn.execute("CREATE SCHEMA cron")
    sync_conn.execute("CREATE TABLE cron.job (jobid bigint, jobname text)")
    sync_conn.execute("CREATE ROLE cl_go_live_outsider NOLOGIN")
    sync_conn.execute("SET LOCAL SESSION AUTHORIZATION cl_go_live_outsider")
    result = glc.guarded(sync_conn, "pg_cron drain", lambda: glc.check_drain_cron(sync_conn))
    assert result.status == "FAIL" and "permission denied" in result.detail
    assert "42501" in result.detail


def test_an_invalid_url_or_a_crashing_check_is_a_fail_line(
    sync_conn: psycopg.Connection,
) -> None:
    invalid = glc.check_health("http://exa mple.test", timeout=2)
    assert invalid.status == "FAIL" and "failed:" in invalid.detail

    def crashing() -> glc.Result:
        raise RuntimeError("boom")

    crashed = glc.guarded(sync_conn, "custom", crashing)
    assert crashed == glc.Result("FAIL", "custom", "check failed: RuntimeError")
