"""Story 2.4 / AD-12: spend caps checked by ``start_provider_run`` before any spend, and the
``GET``/``PUT /api/v1/spend-caps`` endpoints. One test (or more) per matrix row."""

import asyncio
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.main import create_app
from app.modules.accounts import service as accounts_service
from app.modules.listings import repo, service
from app.ports import provider_runner as provider_runner_port
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.provider_runner import FakeProviderRunner
from tests.listing_fixtures import listing_db  # noqa: F401 (fixture)
from tests.provider_run_fixtures import (  # noqa: F401 (fixtures)
    SEED_TOKEN,
    RunsDb,
    insert_run,
    make_student,
    master_key,
    runner,
    runs_db,
)

pytestmark = pytest.mark.usefixtures("clean_jobs", "listing_db")

PARAMS = {"role_titles": ["Backend Engineering Intern"], "locations": [], "job_types": []}
CAPS_URL = "/api/v1/spend-caps"


async def _start(owner: uuid.UUID, day: str = "2026-11-15") -> service.RunOutcome:
    return await service.start_provider_run(
        owner, "jobs", PARAMS, run_key=f"collect:{owner}:{day}"
    )


def _set_caps(db: RunsDb, owner: uuid.UUID, per_run: str, monthly: str) -> None:
    db.execute(
        "INSERT INTO app.spend_caps (owner_id, per_run_usd, monthly_usd) VALUES (%s, %s, %s)",
        (owner, per_run, monthly),
    )


@pytest.fixture
def platform_cap(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PLATFORM_MONTHLY_CAP_USD", "1.00")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("PLATFORM_MONTHLY_CAP_USD", raising=False)
    get_settings.cache_clear()


# --------------------------------------------------------------------------- start


async def test_defaults_set_the_charge_and_the_estimate(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    outcome = await _start(owner)
    assert outcome.status == "running"
    (call,) = runner.started
    assert call.max_charge_usd == 0.25 and call.max_items == 100
    row = runs_db.run(outcome.run_id)
    assert row["max_charge_usd"] == Decimal("0.25")
    assert row["estimated_cost_usd"] == Decimal("0.1000")


async def test_student_caps_set_the_charge(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    _set_caps(runs_db, owner, "0.50", "10")
    assert (await _start(owner)).status == "running"
    assert runner.started[0].max_charge_usd == 0.5


async def test_per_run_cap_refuses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    _set_caps(runs_db, owner, "0.05", "4")
    outcome = await _start(owner)
    assert outcome.status == "refused" and outcome.error_kind == "run_cap"
    assert runner.started == []
    (row,) = runs_db.runs(owner)
    assert row["estimated_cost_usd"] == Decimal("0.1000")
    assert runs_db.ops("listings.provider_run_refused") == [(owner, row["id"], {"run_cap": 1})]


async def test_monthly_cap_refuses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "succeeded", cost="3.90")
    outcome = await _start(owner)
    assert outcome.error_kind == "monthly_cap" and runner.started == []


async def test_monthly_cap_allows_up_to_the_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "succeeded", cost="3.75")  # 3.75 + 0.25 = 4.00
    assert (await _start(owner)).status == "running"


async def test_in_flight_runs_count_at_their_remaining_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "succeeded", cost="3.55")
    insert_run(runs_db, owner, "running", cost="0.05", max_charge="0.25")
    async with get_sessionmaker()() as session:
        spend = await repo.month_spend(session, service.month_start(service.utcnow()), owner)
    assert spend.spent == Decimal("3.60") and spend.committed == Decimal("3.80")
    outcome = await _start(owner)
    assert outcome.error_kind == "monthly_cap" and runner.started == []


async def test_refused_rows_and_last_month_do_not_count(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "succeeded", cost="3.90", days_ago=40)
    insert_run(runs_db, owner, "refused", error_kind="paused")
    insert_run(runs_db, owner, "starting", max_charge="0.25")  # committed 0.25
    assert (await _start(owner)).status == "running"


async def test_platform_cap_refuses(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner, platform_cap: None
) -> None:
    other = accounts.create()
    insert_run(runs_db, other, "succeeded", cost="0.65")
    insert_run(runs_db, other, "running", cost="0.00", max_charge="0.25")  # committed 0.90
    owner = await make_student(accounts, runs_db)
    outcome = await _start(owner)
    assert outcome.error_kind == "platform_cap" and runner.started == []


async def test_platform_cap_ignores_seed_runs(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner, platform_cap: None
) -> None:
    insert_run(runs_db, None, "succeeded", cost="5.00")
    owner = await make_student(accounts, runs_db)
    assert (await _start(owner)).status == "running"


async def test_seed_run_skips_the_caps(
    runs_db: RunsDb, runner: FakeProviderRunner, platform_cap: None
) -> None:
    insert_run(runs_db, None, "succeeded", cost="5.00")
    outcome = await service.start_provider_run(
        None,
        "jobs",
        PARAMS,
        run_key=f"seed:{uuid.uuid7()}",
        token=SecretStr(SEED_TOKEN),
        max_items=1000,
        max_charge_usd=2.0,
    )
    assert outcome.status == "running"
    assert runs_db.run(outcome.run_id)["estimated_cost_usd"] == Decimal("1.0000")


async def test_a_student_run_cannot_raise_its_charge(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    with pytest.raises(ValueError):
        await service.start_provider_run(
            owner, "jobs", PARAMS, run_key=f"collect:{owner}:x", max_charge_usd=5
        )


async def test_resaving_a_key_does_not_override_caps(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    from tests.provider_run_fixtures import store_key

    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "succeeded", cost="3.90")
    await store_key(owner)
    assert (await _start(owner)).error_kind == "monthly_cap"


# --------------------------------------------------------------------------- caps API


@pytest.fixture
def app(runner: FakeProviderRunner):
    saved = provider_runner_port._installed
    yield create_app(provider_runner=runner)
    provider_runner_port._installed = saved


def _headers(accounts: AccountFactory, tokens: TokenFactory, owner: uuid.UUID) -> dict:
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    return tokens.headers(tokens.token(owner, email))


async def test_caps_api_defaults_then_set(
    client: AsyncClient, accounts: AccountFactory, tokens: TokenFactory, runs_db: RunsDb
) -> None:
    owner = accounts.create()
    headers = _headers(accounts, tokens, owner)
    got = await client.get(CAPS_URL, headers=headers)
    assert got.status_code == 200
    assert got.json() == {"per_run_usd": 0.25, "monthly_usd": 4.0, "is_default": True}

    put = await client.put(
        CAPS_URL, headers=headers, json={"per_run_usd": 0.5, "monthly_usd": 10}
    )
    assert put.status_code == 200
    assert put.json() == {"per_run_usd": 0.5, "monthly_usd": 10.0, "is_default": False}
    again = await client.get(CAPS_URL, headers=headers)
    assert again.json() == put.json()
    assert runs_db.execute(
        "SELECT per_run_usd, monthly_usd FROM app.spend_caps WHERE owner_id = %s", (owner,)
    ) == [(Decimal("0.5000"), Decimal("10.0000"))]


@pytest.mark.parametrize(
    "body",
    [
        {"per_run_usd": 3.00, "monthly_usd": 10},
        {"per_run_usd": 0.01, "monthly_usd": 10},
        {"per_run_usd": 0.25, "monthly_usd": 0.10},
        {"per_run_usd": 0.25, "monthly_usd": 101},
        {"per_run_usd": 1.00, "monthly_usd": 0.50},
        {"per_run_usd": 0.25},
    ],
)
async def test_caps_api_rejects_out_of_bounds(
    body: dict,
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
) -> None:
    owner = accounts.create()
    res = await client.put(CAPS_URL, headers=_headers(accounts, tokens, owner), json=body)
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/problem+json")
    assert runs_db.execute(
        "SELECT count(*) FROM app.spend_caps WHERE owner_id = %s", (owner,)
    ) == [(0,)]


async def test_caps_api_needs_a_session(client: AsyncClient, tokens: TokenFactory) -> None:
    assert (await client.get(CAPS_URL)).status_code == 401
    assert (
        await client.put(CAPS_URL, json={"per_run_usd": 0.5, "monthly_usd": 10})
    ).status_code == 401


async def test_set_spend_caps_service_checks_the_order(accounts: AccountFactory) -> None:
    owner = accounts.create()
    async with get_sessionmaker()() as session:
        with pytest.raises(accounts_service.InvalidInputError):
            await accounts_service.set_spend_caps(
                session, owner, Decimal("1.00"), Decimal("0.50")
            )


async def test_spend_caps_cascade_on_account_deletion(
    accounts: AccountFactory, runs_db: RunsDb
) -> None:
    owner = accounts.create()
    _set_caps(runs_db, owner, "0.25", "4")
    runs_db.execute("DELETE FROM app.accounts WHERE id = %s", (owner,))
    assert runs_db.execute(
        "SELECT count(*) FROM app.spend_caps WHERE owner_id = %s", (owner,)
    ) == [(0,)]


# --------------------------------------------------------------------------- spend accounting


async def _month(owner: uuid.UUID | None = None) -> repo.MonthSpend:
    async with get_sessionmaker()() as session:
        return await repo.month_spend(session, service.month_start(service.utcnow()), owner)


async def test_failed_run_with_no_recorded_cost_counts_at_its_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    # start_unknown / timeout / reaped: reached Apify (run id), cost never recorded.
    insert_run(runs_db, owner, "failed", error_kind="timeout", max_charge="0.25")
    # Failed before Apify was called (no run id): spent nothing.
    no_id = insert_run(runs_db, owner, "failed", error_kind="unavailable", max_charge="0.25")
    runs_db.execute("UPDATE app.provider_runs SET apify_run_id = NULL WHERE id = %s", (no_id,))
    # Failed with a recorded cost: that cost.
    insert_run(runs_db, owner, "failed", error_kind="failed", cost="0.03")
    spend = await _month(owner)
    assert spend.spent == Decimal("0.28") and spend.committed == Decimal("0.28")


async def test_failed_null_cost_runs_count_against_the_monthly_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    _set_caps(runs_db, owner, "0.25", "0.50")
    insert_run(runs_db, owner, "failed", error_kind="poll_exhausted", max_charge="0.25")
    assert (await _start(owner)).status == "running"  # 0.25 + 0.25 = 0.50 fits
    assert (await _start(owner, "2026-11-16")).error_kind == "monthly_cap"


async def test_in_flight_run_from_last_month_still_counts(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    insert_run(runs_db, owner, "running", cost="0.05", max_charge="0.25", days_ago=40)
    insert_run(runs_db, owner, "succeeded", cost="2.00", days_ago=40)  # finished: not counted
    spend = await _month(owner)
    assert spend.committed == Decimal("0.25")
    _set_caps(runs_db, owner, "0.25", "0.25")
    assert (await _start(owner)).error_kind == "monthly_cap"


# --------------------------------------------------------------------------- concurrency


async def test_two_concurrent_starts_cannot_both_pass_the_monthly_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner
) -> None:
    owner = await make_student(accounts, runs_db)
    _set_caps(runs_db, owner, "0.25", "0.25")  # room for exactly one run
    a, b = await asyncio.gather(_start(owner, "2026-11-15"), _start(owner, "2026-11-16"))
    assert sorted(o.status for o in (a, b)) == ["refused", "running"]
    refused = a if a.status == "refused" else b
    assert refused.error_kind == "monthly_cap"
    assert len(runner.started) == 1


async def test_two_students_cannot_both_pass_the_platform_cap(
    accounts: AccountFactory, runs_db: RunsDb, runner: FakeProviderRunner, platform_cap: None
) -> None:
    other = accounts.create()
    insert_run(runs_db, other, "succeeded", cost="0.60")  # 1.00 cap: room for one 0.25 run
    first = await make_student(accounts, runs_db)
    second = await make_student(accounts, runs_db)
    a, b = await asyncio.gather(_start(first), _start(second))
    assert sorted(o.status for o in (a, b)) == ["refused", "running"]
    refused = a if a.status == "refused" else b
    assert refused.error_kind == "platform_cap"
    assert len(runner.started) == 1
