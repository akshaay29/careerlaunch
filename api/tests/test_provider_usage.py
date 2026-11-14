"""Story 2.4: ``GET /api/v1/provider-usage`` (status, caps, month, latest runs) and the
acceptance flow: credit exhausted on a poll pauses until the cycle end, then resumes."""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.core.clock import utcnow
from app.main import create_app
from app.modules.listings import service, tasks
from app.ports import provider_runner as provider_runner_port
from app.ports.provider_runner import ProviderRejected
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.provider_runner import FakeProviderRunner
from tests.listing_fixtures import listing_db  # noqa: F401 (fixture)
from tests.provider_run_fixtures import (  # noqa: F401 (fixtures)
    TOKEN,
    RunsDb,
    insert_run,
    make_student,
    master_key,
    runner,
    runs_db,
)

pytestmark = pytest.mark.usefixtures("clean_jobs", "listing_db")

USAGE_URL = "/api/v1/provider-usage"
PARAMS = {"role_titles": ["Backend Engineering Intern"], "locations": [], "job_types": []}


@pytest.fixture
def app(runner: FakeProviderRunner):
    saved = provider_runner_port._installed
    yield create_app(provider_runner=runner)
    provider_runner_port._installed = saved


def _headers(accounts: AccountFactory, tokens: TokenFactory, owner: uuid.UUID) -> dict:
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    return tokens.headers(tokens.token(owner, email))


async def test_usage_shows_only_my_runs_without_ids_or_params(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    me = accounts.create()
    other = accounts.create()
    insert_run(runs_db, other, "succeeded", cost="1.00", items=9, ingested=9)
    insert_run(runs_db, me, "succeeded", cost="0.08", items=10, ingested=8)
    insert_run(runs_db, me, "running", cost="0.02", max_charge="0.25")
    insert_run(runs_db, me, "refused", error_kind="monthly_cap")
    # Well before this UTC month (45 days back): never in the month aggregate.
    insert_run(runs_db, me, "succeeded", cost="2.00", days_ago=45)

    res = await client.get(USAGE_URL, headers=_headers(accounts, tokens, me))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == {"status": "active", "reason": None, "exhausted_until": None}
    assert body["caps"] == {"per_run_usd": 0.25, "monthly_usd": 4.0, "is_default": True}
    month = body["month"]
    assert month["spent_usd"] == pytest.approx(0.10)
    assert month["committed_usd"] == pytest.approx(0.33)
    assert month["monthly_cap_usd"] == 4.0

    runs = body["runs"]
    assert len(runs) == 4  # only mine, newest first
    assert [r["status"] for r in runs[:2]] == ["refused", "running"]
    assert runs[0]["error_kind"] == "monthly_cap"
    assert set(runs[0]) == {
        "created_at",
        "status",
        "error_kind",
        "estimated_cost_usd",
        "cost_usd",
        "items",
        "ingested",
    }
    text = res.text
    for secret in ("fakerun", "Secret Role", "params", "run_key", "apify", TOKEN):
        assert secret not in text, secret
    assert str(other) not in text


async def test_usage_keeps_the_latest_30_runs(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    me = accounts.create()
    for _ in range(32):
        insert_run(runs_db, me, "refused", error_kind="paused")
    res = await client.get(USAGE_URL, headers=_headers(accounts, tokens, me))
    assert len(res.json()["runs"]) == 30


async def test_usage_needs_a_session(client: AsyncClient, tokens: TokenFactory) -> None:
    assert (await client.get(USAGE_URL)).status_code == 401


async def test_credit_exhausted_pauses_until_the_cycle_end_then_resumes(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    runs_db: RunsDb,
    runner: FakeProviderRunner,
) -> None:
    """Acceptance: a saved key, then exhausted credit reported on a poll."""
    owner = await make_student(accounts, runs_db)  # saves the key (ProviderKeyChanged)
    headers = _headers(accounts, tokens, owner)
    first = await service.start_provider_run(
        owner, "jobs", PARAMS, run_key=f"collect:{owner}:d1"
    )
    assert first.status == "running"

    cycle_end = utcnow() + timedelta(days=3)
    runner.cycle_end = cycle_end
    runner.get_error = ProviderRejected("x", kind="credit")  # type: ignore[assignment]
    await tasks.poll_provider_run(provider_run_id=first.run_id)

    status = (await client.get(USAGE_URL, headers=headers)).json()["status"]
    assert status["status"] == "paused" and status["reason"] == "credit_exhausted"
    assert status["exhausted_until"] is not None

    # Later collects are refused without a runner call.
    later = await service.start_provider_run(
        owner, "jobs", PARAMS, run_key=f"collect:{owner}:d2"
    )
    assert later.status == "refused" and later.error_kind == "exhausted"
    assert len(runner.started) == 1

    # The cycle resets: the next collect starts a run, and usage reads active again.
    runs_db.execute(
        "UPDATE app.provider_status SET exhausted_until = now() - interval '1 minute' "
        "WHERE owner_id = %s",
        (owner,),
    )
    runner.get_error = None
    resumed = await service.start_provider_run(
        owner, "jobs", PARAMS, run_key=f"collect:{owner}:d3"
    )
    assert resumed.status == "running" and len(runner.started) == 2
    status = (await client.get(USAGE_URL, headers=headers)).json()["status"]
    assert status == {"status": "active", "reason": None, "exhausted_until": None}
