"""Shared helpers for the collection tests (Story 2.3): actor item fixtures shaped like the
Apify Store samples, a fake-runner environment and Student setup."""

import base64
import itertools
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr

from app.adapters.apify import ApifyLinkedInSource
from app.core.config import get_settings, to_libpq_url
from app.core.db import get_sessionmaker
from app.modules.accounts import service as accounts_service
from app.modules.accounts.consent_text import ConsentPurpose, current_text
from app.ports import provider_runner as provider_runner_port
from app.ports import source_adapter as source_adapter_port
from tests.conftest import AccountFactory
from tests.fakes.provider_runner import FakeProviderRunner

TOKEN = "apify_api_CollectToken0123456789abcdefWXYZ"  # ggignore -- fake test token
SEED_TOKEN = "apify_api_SeedOwnerToken9876543210zyxwQRST"  # ggignore -- fake test token
PRIMARY = "curious_coder/linkedin-jobs-scraper"
FALLBACK = "harvestapi/linkedin-job-search"
DESCRIPTION_WORD = "quokkaplatypus"


def curious_item(n: int, **overrides: Any) -> dict[str, Any]:
    """An item shaped like curious_coder/linkedin-jobs-scraper's documented sample."""
    item: dict[str, Any] = {
        "id": str(3692563200 + n),
        "trackingId": "abc",
        "refId": "def",
        "link": f"https://www.linkedin.com/jobs/view/backend-intern-{3692563200 + n}?refId=x&trk=y",
        "title": f"Backend Engineering Intern {n}",
        "companyName": "Facebook",
        "companyLinkedinUrl": "https://www.linkedin.com/company/facebook",
        "location": "Bengaluru, Karnataka, India",
        "salaryInfo": ["$17.00", "$19.00"],
        "postedAt": "2026-11-10",
        "benefits": [],
        "descriptionText": f"We use Python and PostgreSQL. {DESCRIPTION_WORD} welcome.",
        "descriptionHtml": "<p>html</p>",
        "applicantsCount": "200",
        "applyUrl": "",
        "seniorityLevel": "Internship",
        "employmentType": "Internship",
        "jobFunction": "Engineering",
        "industries": "Software Development",
        "jobPosterName": "Someone Private",
    }
    item.update(overrides)
    return item


def harvest_item(n: int, **overrides: Any) -> dict[str, Any]:
    """An item shaped like harvestapi/linkedin-job-search's documented sample."""
    item: dict[str, Any] = {
        "id": str(4227647589 + n),
        "title": f"Associate Data Engineer {n}",
        "linkedinUrl": f"https://www.linkedin.com/jobs/view/{4227647589 + n}/",
        "jobState": "LISTED",
        "postedDate": "2026-11-12T17:12:41.000Z",
        "expireAt": "2026-12-12T17:12:41.000Z",
        "descriptionText": f"About the Company. {DESCRIPTION_WORD}. SQL and Airflow.",
        "employmentType": "full_time",
        "workplaceType": "on_site",
        "location": {
            "linkedinText": "Greenwood Village, CO",
            "countryCode": "us",
            "geoId": "101851648",
            "parsed": {"text": "Greenwood Village, CO, United States", "city": "Greenwood Village"},
        },
        "company": {"id": "1", "name": "East Daley Analytics", "employeeCount": 50},
    }
    item.update(overrides)
    return item


def curious_dataset(valid: int, invalid: int) -> list[dict[str, Any]]:
    """``valid`` good items followed by ``invalid`` unusable ones."""
    items = [curious_item(n) for n in range(1, valid + 1)]
    bad: list[Any] = [
        curious_item(900, id=None),
        curious_item(901, title=""),
        curious_item(902, id="not-a-number"),
        "not an object",
    ]
    return items + [bad[i % len(bad)] for i in range(invalid)]


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(to_libpq_url(url), autocommit=True)


@dataclass
class RunsDb:
    url: str

    def execute(self, sql: str, params: tuple | dict | None = None) -> list[tuple]:
        with connect(self.url) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def runs(self, owner_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        with connect(self.url) as conn:
            if owner_id is None:
                cur = conn.execute("SELECT * FROM app.provider_runs ORDER BY created_at")
            else:
                cur = conn.execute(
                    "SELECT * FROM app.provider_runs WHERE owner_id = %s ORDER BY created_at",
                    (owner_id,),
                )
            cols = [c.name for c in cur.description or ()]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def run(self, run_id: uuid.UUID) -> dict[str, Any]:
        (row,) = [r for r in self.runs() if r["id"] == run_id]
        return row

    def outbox(self, task: str) -> list[tuple]:
        return self.execute(
            "SELECT key, run_at, args FROM app.job_outbox WHERE task = %s ORDER BY created_at",
            (task,),
        )

    def ops(self, kind: str) -> list[tuple]:
        return self.execute(
            "SELECT owner_id, entity_id, counts FROM app.ops_events WHERE kind = %s "
            "ORDER BY created_at",
            (kind,),
        )

    def grant(self, owner_id: uuid.UUID, purpose: ConsentPurpose) -> None:
        text = current_text(purpose)
        self.execute(
            "INSERT INTO app.consents (id, owner_id, purpose, version) VALUES (%s, %s, %s, %s)",
            (uuid.uuid7(), owner_id, text.purpose.value, text.version),
        )

    def set_preferences(
        self,
        owner_id: uuid.UUID,
        role_titles: list[str],
        locations: list[str] | None = None,
        job_types: list[str] | None = None,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self.execute(
            "INSERT INTO app.preferences (owner_id, role_titles, locations, job_types, timezone) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (owner_id) DO UPDATE SET "
            "role_titles = EXCLUDED.role_titles, locations = EXCLUDED.locations, "
            "job_types = EXCLUDED.job_types, timezone = EXCLUDED.timezone",
            (owner_id, role_titles, locations or [], job_types or [], timezone),
        )


@pytest.fixture
def runs_db(migrated_db: str) -> Iterator[RunsDb]:
    db = RunsDb(migrated_db)
    db.execute("DELETE FROM app.provider_runs WHERE owner_id IS NULL")
    yield db
    db.execute("DELETE FROM app.provider_runs WHERE owner_id IS NULL")


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    value = "1:" + base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("CREDENTIALS_MASTER_KEYS", value)
    get_settings.cache_clear()
    yield value
    monkeypatch.delenv("CREDENTIALS_MASTER_KEYS", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def runner(master_key: str) -> Iterator[FakeProviderRunner]:
    """A FakeProviderRunner and the LinkedIn mapping installed process-wide (restored
    afterwards)."""
    saved_runner = provider_runner_port._installed
    saved_source = source_adapter_port._provider_source
    fake = FakeProviderRunner()
    provider_runner_port.install_provider_runner(fake)
    source_adapter_port.install_provider_source(ApifyLinkedInSource())
    yield fake
    provider_runner_port._installed = saved_runner
    source_adapter_port._provider_source = saved_source


async def store_key(owner_id: uuid.UUID, token: str = TOKEN) -> None:
    """Save a Provider Key the way the router does (publishes ProviderKeyChanged)."""
    async with get_sessionmaker()() as session:
        await accounts_service.store_credential(session, owner_id, SecretStr(token), "u1")
        await session.commit()


async def make_student(
    accounts: AccountFactory,
    db: RunsDb,
    *,
    provider_runs: bool = True,
    pool: bool = True,
    key: bool = True,
    role_titles: list[str] | None = None,
) -> uuid.UUID:
    """An account with consents, a stored key and Preferences (role titles default to one)."""
    owner = accounts.create()
    if key:
        db.grant(owner, ConsentPurpose.PROVIDER_RUNS)
        await store_key(owner)
    if not provider_runs and key:
        db.execute(
            "UPDATE app.consents SET revoked_at = now(), end_reason = 'revoked' "
            "WHERE owner_id = %s AND purpose = 'provider_runs'",
            (owner,),
        )
    elif provider_runs and not key:
        db.grant(owner, ConsentPurpose.PROVIDER_RUNS)
    if pool:
        db.grant(owner, ConsentPurpose.POOL_CONTRIBUTION)
    db.set_preferences(
        owner,
        ["Backend Engineering Intern"] if role_titles is None else role_titles,
        ["Bengaluru, Karnataka, India"],
        ["internship"],
    )
    return owner


_fake_ids = itertools.count(1)


def insert_run(
    db: RunsDb,
    owner_id: uuid.UUID | None,
    status: str,
    *,
    cost: str | None = None,
    max_charge: str = "0.25",
    days_ago: int = 0,
    error_kind: str | None = None,
    items: int | None = None,
    ingested: int | None = None,
) -> uuid.UUID:
    """A ``provider_runs`` row written directly (Story 2.4 spend and usage tests). Running and
    succeeded rows get a low-entropy fake Apify run id."""
    run_id = uuid.uuid7()
    n = next(_fake_ids)
    apify_id = f"fakerun{n:04d}" if status in ("running", "succeeded", "failed") else None
    db.execute(
        "INSERT INTO app.provider_runs (id, owner_id, purpose, run_key, actor, status, "
        "apify_run_id, params, max_items, max_charge_usd, estimated_cost_usd, cost_usd, "
        "error_kind, items, ingested, created_at) VALUES (%s, %s, 'jobs', %s, %s, %s, %s, "
        "'{\"role_titles\": [\"Secret Role\"]}'::jsonb, 100, %s, 0.10, %s, %s, %s, %s, "
        "now() - make_interval(days => %s))",
        (
            run_id,
            owner_id,
            f"test:{run_id}",
            PRIMARY,
            status,
            apify_id,
            max_charge,
            cost,
            error_kind,
            items,
            ingested,
            days_ago,
        ),
    )
    return run_id
