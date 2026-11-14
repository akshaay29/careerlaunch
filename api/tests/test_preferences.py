"""FR-5: Preferences. One test (or more) per I/O-matrix row of Story 1.7."""

import asyncio
import uuid
import zoneinfo
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core import events
from app.core.config import to_libpq_url
from app.core.events import PreferencesChanged, subscribe
from app.modules.profile import router as profile_router
from app.modules.profile import schemas as profile_schemas
from tests.conftest import AccountFactory, TokenFactory

PREFS = "/api/v1/preferences"
DEGREES = "/api/v1/degrees"
PROBLEM = "application/problem+json"

pytestmark = pytest.mark.usefixtures("clean_jobs")

Student = tuple[uuid.UUID, dict[str, str]]

DEFAULTS = {
    "role_titles": [],
    "locations": [],
    "job_types": [],
    "batch_year": None,
    "cgpa": None,
    "degree": None,
    "degree_other": None,
    "work_authorisation": None,
    "threshold": 60,
    "alert_frequency": "daily",
    "timezone": "Asia/Kolkata",
}

VALID = {
    "role_titles": ["Backend Intern", "Data Analyst"],
    "locations": ["Bengaluru", "Remote"],
    "job_types": ["internship", "full_time"],
    "batch_year": 2027,
    "cgpa": 8.5,
    "degree": "btech",
    "degree_other": None,
    "work_authorisation": "india_authorised",
    "threshold": 60,
    "alert_frequency": "weekly",
    "timezone": "Asia/Kolkata",
}


def _student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, tokens.headers(tokens.token(account_id, email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def other_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def changed() -> Iterator[list[PreferencesChanged]]:
    """Record every PreferencesChanged; the registry is restored afterwards."""
    saved = {k: list(v) for k, v in events._subscribers.items()}
    seen: list[PreferencesChanged] = []

    async def record(session: Any, event: PreferencesChanged) -> None:
        seen.append(event)

    subscribe(PreferencesChanged, record)
    yield seen
    events.clear_subscribers()
    events._subscribers.update(saved)


@pytest.fixture
def drains(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the after-response priority drains the router starts."""
    calls: list[int] = []

    async def counted() -> None:
        calls.append(1)

    monkeypatch.setattr(profile_router, "drain_priority", counted)
    return calls


def _row(url: str, owner_id: uuid.UUID) -> tuple | None:
    with psycopg.connect(to_libpq_url(url)) as conn:
        return conn.execute(
            "SELECT role_titles, locations, job_types, batch_year, cgpa, degree, degree_other, "
            "work_authorisation, threshold, alert_frequency, timezone, updated_at "
            "FROM app.preferences WHERE owner_id = %s",
            (owner_id,),
        ).fetchone()


async def _put(client: AsyncClient, headers: dict[str, str], **overrides: Any):
    return await client.put(PREFS, json={**VALID, **overrides}, headers=headers)


def _assert_422(resp, field: str) -> None:
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == "/problems/validation-error"
    locs = [e["loc"] for e in body["errors"]]
    assert any(field in loc for loc in locs), locs


# --------------------------------------------------------------------------- defaults


async def test_defaults_without_a_row(client: AsyncClient, student: Student, migrated_db: str):
    owner, headers = student
    resp = await client.get(PREFS, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**DEFAULTS, "saved": False, "updated_at": None}
    assert _row(migrated_db, owner) is None


async def test_defaults_can_be_saved_as_they_are(
    client: AsyncClient, student: Student, changed: list[PreferencesChanged], migrated_db: str
):
    """The SM-6 path: accepting the defaults is a valid save with nothing filled in."""
    owner, headers = student
    resp = await client.put(PREFS, json=DEFAULTS, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["saved"] is True
    assert changed == [PreferencesChanged(owner_id=owner)]
    assert _row(migrated_db, owner) is not None


# --------------------------------------------------------------------------- saves


async def test_first_save_writes_one_row_and_publishes_once_before_commit(
    client: AsyncClient,
    student: Student,
    changed: list[PreferencesChanged],
    drains: list[int],
    migrated_db: str,
):
    owner, headers = student
    seen_in_session: list[int | None] = []
    committed_view: list[tuple | None] = []

    async def inspect(session: Any, event: PreferencesChanged) -> None:
        result = await session.execute(
            text("SELECT threshold FROM preferences WHERE owner_id = :o"), {"o": event.owner_id}
        )
        seen_in_session.append(result.scalar_one_or_none())
        committed_view.append(_row(migrated_db, event.owner_id))

    subscribe(PreferencesChanged, inspect)
    resp = await _put(client, headers, threshold=70)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True and body["updated_at"] is not None
    assert {k: body[k] for k in VALID} == {**VALID, "threshold": 70}

    assert changed == [PreferencesChanged(owner_id=owner)]
    # The subscriber saw the new row in the publisher's session, before the commit.
    assert seen_in_session == [70]
    assert committed_view == [None]
    row = _row(migrated_db, owner)
    assert row is not None and row[8] == 70
    assert drains == [1]

    got = (await client.get(PREFS, headers=headers)).json()
    assert got == body


async def test_unchanged_save_writes_nothing_and_publishes_nothing(
    client: AsyncClient,
    student: Student,
    changed: list[PreferencesChanged],
    drains: list[int],
    migrated_db: str,
):
    owner, headers = student
    assert (await _put(client, headers)).status_code == 200
    before = _row(migrated_db, owner)
    changed.clear()
    drains.clear()
    # The same effective values, written differently.
    resp = await _put(
        client,
        headers,
        role_titles=[" Backend Intern ", "backend intern", "", "Data   Analyst"],
        job_types=["full_time", "internship", "internship"],
        cgpa=8.50,
    )
    assert resp.status_code == 200, resp.text
    assert changed == [] and drains == []
    assert _row(migrated_db, owner) == before  # updated_at unchanged too


async def test_change_updates_the_row_and_publishes_once(
    client: AsyncClient, student: Student, changed: list[PreferencesChanged], migrated_db: str
):
    owner, headers = student
    await _put(client, headers)
    changed.clear()
    resp = await _put(client, headers, threshold=75)
    assert resp.status_code == 200 and resp.json()["threshold"] == 75
    assert changed == [PreferencesChanged(owner_id=owner)]
    row = _row(migrated_db, owner)
    assert row is not None and row[8] == 75


async def test_normalisation(client: AsyncClient, student: Student, migrated_db: str):
    owner, headers = student
    resp = await _put(
        client,
        headers,
        role_titles=[" Backend Intern ", "backend intern", ""],
        locations=["  ", "Pune", "PUNE", "New   Delhi"],
        job_types=["full_time", "internship", "full_time"],
        cgpa=7.456,
        degree="other",
        degree_other="  B.Des  ",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role_titles"] == ["Backend Intern"]
    assert body["locations"] == ["Pune", "New Delhi"]
    assert body["job_types"] == ["internship", "full_time"]
    assert body["cgpa"] == 7.46
    assert body["degree_other"] == "B.Des"
    row = _row(migrated_db, owner)
    assert row is not None and row[0] == ["Backend Intern"] and str(row[4]) == "7.46"


async def test_concurrent_identical_first_saves_publish_once(
    client: AsyncClient,
    student: Student,
    changed: list[PreferencesChanged],
    drains: list[int],
    migrated_db: str,
):
    owner, headers = student
    results = await asyncio.gather(_put(client, headers), _put(client, headers))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    with psycopg.connect(to_libpq_url(migrated_db)) as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM app.preferences WHERE owner_id = %s", (owner,)
        ).fetchone()  # type: ignore[misc]
    assert count == 1
    assert changed == [PreferencesChanged(owner_id=owner)]
    assert drains == [1]


async def test_indic_location_with_zwj_is_saved(client: AsyncClient, student: Student):
    """ZWJ / ZWNJ are part of Indic spelling and must not be rejected as control chars."""
    location = "क्‍षेत्र"  # Devanagari with ZWJ
    resp = await _put(client, student[1], locations=[location, "क‌ष"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["locations"] == [location, "क‌ष"]


@pytest.mark.parametrize("char", ["‮", "⁦", ""])
async def test_bidi_and_control_chars_are_422(client: AsyncClient, student: Student, char: str):
    _assert_422(await _put(client, student[1], locations=[f"Pune{char}x"]), "locations")


def test_time_zone_database_is_available():
    """tzdata is a direct dependency: without it every PUT would be a 422."""
    assert "Asia/Kolkata" in zoneinfo.available_timezones()
    assert "Asia/Kolkata" in profile_schemas._timezones()


async def test_blank_degree_other_is_null(client: AsyncClient, student: Student):
    _, headers = student
    resp = await _put(client, headers, degree="other", degree_other="   ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["degree_other"] is None


async def test_nullable_fields_may_be_null(client: AsyncClient, student: Student):
    _, headers = student
    nulls = {"batch_year": None, "cgpa": None, "degree": None, "work_authorisation": None}
    resp = await _put(client, headers, **nulls)
    assert resp.status_code == 200, resp.text
    assert {k: resp.json()[k] for k in nulls} == nulls


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("threshold", [-1, 101])
async def test_threshold_bounds(
    client: AsyncClient,
    student: Student,
    changed: list[PreferencesChanged],
    migrated_db: str,
    threshold: int,
):
    owner, headers = student
    resp = await _put(client, headers, threshold=threshold)
    _assert_422(resp, "threshold")
    assert resp.json()["errors"][0]["loc"][-1] == "threshold"
    assert _row(migrated_db, owner) is None and changed == []


@pytest.mark.parametrize("threshold", [0, 100])
async def test_threshold_edges_are_valid(client: AsyncClient, student: Student, threshold: int):
    resp = await _put(client, student[1], threshold=threshold)
    assert resp.status_code == 200 and resp.json()["threshold"] == threshold


async def test_db_backstop_rejects_threshold_101(
    client: AsyncClient, student: Student, migrated_db: str
):
    owner, headers = student
    await _put(client, headers)
    with psycopg.connect(to_libpq_url(migrated_db)) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match="preferences_threshold_check"):
            conn.execute("UPDATE app.preferences SET threshold = 101 WHERE owner_id = %s", (owner,))
        conn.rollback()


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("job_types", ["contract"], "preferences_job_types_check"),
        ("alert_frequency", "hourly", "preferences_alert_frequency_check"),
        ("degree", "xyz", "preferences_degree_check"),
        ("work_authorisation", "x", "preferences_work_authorisation_check"),
        ("cgpa", 10.5, "preferences_cgpa_check"),
        ("batch_year", 1999, "preferences_batch_year_check"),
        ("role_titles", [f"t{i}" for i in range(11)], "preferences_role_titles_check"),
        ("role_titles", ["x" * 81], "preferences_role_titles_check"),
        ("role_titles", [""], "preferences_role_titles_check"),
        ("locations", ["x" * 81], "preferences_locations_check"),
        ("degree_other", "B.Des", "preferences_degree_other_check"),
        ("degree_other_blank", "   ", "preferences_degree_other_check"),
    ],
)
def test_db_backstops(
    sync_conn: psycopg.Connection, accounts: AccountFactory, column: str, value: Any, constraint: str
):
    owner = accounts.create()
    if column == "degree":
        sql = "INSERT INTO app.preferences (owner_id, degree) VALUES (%s, %s)"
    elif column == "degree_other_blank":
        sql = "INSERT INTO app.preferences (owner_id, degree, degree_other) VALUES (%s, 'other', %s)"
    else:
        sql = f"INSERT INTO app.preferences (owner_id, degree, {column}) VALUES (%s, 'btech', %s)"
    with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
        sync_conn.execute(sql, (owner, value))


async def test_bad_time_zone(client: AsyncClient, student: Student, migrated_db: str):
    owner, headers = student
    _assert_422(await _put(client, headers, timezone="Mars/Base"), "timezone")
    _assert_422(await _put(client, headers, timezone=""), "timezone")
    _assert_422(await _put(client, headers, timezone="../etc/passwd"), "timezone")
    assert _row(migrated_db, owner) is None


async def test_other_valid_time_zone(client: AsyncClient, student: Student):
    resp = await _put(client, student[1], timezone="America/New_York")
    assert resp.status_code == 200 and resp.json()["timezone"] == "America/New_York"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_types", ["contract"]),
        ("alert_frequency", "hourly"),
        ("alert_frequency", "instant"),
        ("degree", "xyz"),
        ("degree", "B.Tech"),
        ("work_authorisation", "x"),
        ("cgpa", 10.5),
        ("cgpa", -0.1),
        ("batch_year", 1999),
        ("batch_year", 2101),
        ("role_titles", [f"Role {i}" for i in range(11)]),
        ("role_titles", ["x" * 81]),
        ("locations", [f"City {i}" for i in range(11)]),
        ("locations", ["y" * 81]),
        ("role_titles", ["bad\u0000title"]),
        ("role_titles", "Backend Intern"),
        ("threshold", True),
        ("threshold", "60"),
        ("threshold", 60.5),
        ("batch_year", True),
        ("batch_year", "2027"),
        ("cgpa", "8.5"),
        ("cgpa", True),
    ],
)
async def test_bad_values_are_422(
    client: AsyncClient,
    student: Student,
    changed: list[PreferencesChanged],
    migrated_db: str,
    field: str,
    value: Any,
):
    owner, headers = student
    _assert_422(await _put(client, headers, **{field: value}), field)
    assert _row(migrated_db, owner) is None and changed == []


async def test_every_violation_is_reported(client: AsyncClient, student: Student):
    resp = await _put(
        client,
        student[1],
        job_types=["contract"],
        alert_frequency="hourly",
        degree="xyz",
        work_authorisation="x",
    )
    assert resp.status_code == 422
    fields = {e["loc"][1] for e in resp.json()["errors"]}
    assert {"job_types", "alert_frequency", "degree", "work_authorisation"} <= fields


async def test_ten_items_after_dedupe_are_fine(client: AsyncClient, student: Student):
    titles = [f"Role {i}" for i in range(10)] + ["role 0", "  "]
    resp = await _put(client, student[1], role_titles=titles)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["role_titles"]) == 10


async def test_degree_other_only_with_other(
    client: AsyncClient, student: Student, migrated_db: str
):
    owner, headers = student
    _assert_422(await _put(client, headers, degree="btech", degree_other="B.Des"), "degree_other")
    _assert_422(await _put(client, headers, degree=None, degree_other="B.Des"), "degree_other")
    _assert_422(
        await _put(client, headers, degree="other", degree_other="z" * 81), "degree_other"
    )
    assert _row(migrated_db, owner) is None
    resp = await _put(client, headers, degree="other", degree_other="B.Des")
    assert resp.status_code == 200 and resp.json()["degree_other"] == "B.Des"


async def test_put_needs_every_field(client: AsyncClient, student: Student):
    _, headers = student
    partial = {k: v for k, v in VALID.items() if k != "threshold"}
    _assert_422(await client.put(PREFS, json=partial, headers=headers), "threshold")
    partial = {k: v for k, v in VALID.items() if k != "cgpa"}
    _assert_422(await client.put(PREFS, json=partial, headers=headers), "cgpa")
    _assert_422(await _put(client, headers, unexpected=1), "unexpected")


# --------------------------------------------------------------------------- degrees


async def test_degree_list(client: AsyncClient, student: Student):
    resp = await client.get(DEGREES, headers=student[1])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body[0] == {"id": "btech", "label": "B.Tech / B.E."}
    assert body[-1] == {"id": "other", "label": "Other"}
    assert len(body) == 25


# --------------------------------------------------------------------------- isolation, auth


async def test_isolation_and_auth(client: AsyncClient, student: Student, other_student: Student):
    _, headers = student
    _, other_headers = other_student
    await _put(client, headers, threshold=80)
    other = (await client.get(PREFS, headers=other_headers)).json()
    assert other["saved"] is False and other["threshold"] == 60
    await _put(client, other_headers, threshold=40)
    assert (await client.get(PREFS, headers=headers)).json()["threshold"] == 80

    for method, url in [("GET", PREFS), ("PUT", PREFS), ("GET", DEGREES)]:
        resp = await client.request(method, url, json=VALID if method == "PUT" else None)
        assert resp.status_code == 401, (method, url)
        assert resp.headers["content-type"].startswith(PROBLEM)


# --------------------------------------------------------------------------- deletion


async def test_account_deletion_cascades_preferences(
    client: AsyncClient, student: Student, migrated_db: str
):
    owner, headers = student
    await _put(client, headers)
    assert _row(migrated_db, owner) is not None
    with psycopg.connect(to_libpq_url(migrated_db), autocommit=True) as conn:
        conn.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert _row(migrated_db, owner) is None


def test_preferences_table_has_forced_rls_and_no_exposed_grants(
    sync_conn: psycopg.Connection,
) -> None:
    rls = sync_conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.preferences'::regclass"
    ).fetchone()
    assert rls == (True, True)
    grants = sync_conn.execute(
        "SELECT grantee FROM information_schema.role_table_grants "
        "WHERE table_schema = 'app' AND table_name = 'preferences' "
        "AND grantee IN ('anon', 'authenticated', 'PUBLIC')"
    ).fetchall()
    assert grants == []
