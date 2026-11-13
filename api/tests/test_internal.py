"""AD-4 / AD-14: /internal/* secret, drain trigger and drain health."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from app.core import internal as internal_module
from app.core.db import get_sessionmaker
from app.core.jobs import enqueue, task

PROBLEM = "application/problem+json"
RAN: list[uuid.UUID] = []

pytestmark = pytest.mark.usefixtures("clean_jobs")


@task("tests.internal_noop")
async def internal_noop(*, item_id: uuid.UUID) -> None:
    RAN.append(item_id)


@pytest.fixture
def drain_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def fake_drain() -> None:
        calls.append(1)

    monkeypatch.setattr(internal_module, "drain", fake_drain)
    return calls


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _set_last_drain(at: datetime | None) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "INSERT INTO drain_state (id, last_drain_at) VALUES (1, :at) "
                "ON CONFLICT (id) DO UPDATE SET last_drain_at = EXCLUDED.last_drain_at"
            ),
            {"at": at},
        )
        await session.commit()


def _assert_problem(resp, status: int, slug: str) -> None:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == f"/problems/{slug}"


# --------------------------------------------------------------------------- secret


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dGVzdA=="},
        {"Authorization": "test-drain-secret-current"},
    ],
    ids=["missing", "wrong", "empty", "basic", "no-scheme"],
)
async def test_bad_secret_is_401_and_nothing_runs(
    client: AsyncClient, drain_secrets, drain_calls: list[int], headers: dict
) -> None:
    resp = await client.post("/internal/jobs/drain", headers=headers)
    _assert_problem(resp, 401, "unauthorized")
    assert drain_calls == []
    _assert_problem(await client.get("/internal/health", headers=headers), 401, "unauthorized")


async def test_no_secret_configured_rejects_everything(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, drain_calls: list[int]
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("DRAIN_SECRET", "")
    monkeypatch.setenv("DRAIN_SECRET_PREVIOUS", "")
    get_settings.cache_clear()
    try:
        resp = await client.post("/internal/jobs/drain", headers=_auth(""))
        _assert_problem(resp, 401, "unauthorized")
        resp = await client.post("/internal/jobs/drain", headers=_auth("anything"))
        _assert_problem(resp, 401, "unauthorized")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert drain_calls == []


@pytest.mark.parametrize("which", [0, 1], ids=["current", "previous"])
async def test_valid_secret_accepts_drain(
    client: AsyncClient, drain_secrets: tuple[str, str], drain_calls: list[int], which: int
) -> None:
    resp = await client.post("/internal/jobs/drain", headers=_auth(drain_secrets[which]))
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}
    assert drain_calls == [1]


async def test_drain_endpoint_runs_real_drain(
    client: AsyncClient, drain_secrets: tuple[str, str]
) -> None:
    RAN.clear()
    item = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await enqueue(session, internal_noop, f"tests.internal_noop:item:{item}", item_id=item)
        await session.commit()

    resp = await client.post("/internal/jobs/drain", headers=_auth(drain_secrets[0]))

    assert resp.status_code == 202
    # httpx's ASGI transport returns after background tasks finish.
    assert RAN == [item]
    health = await client.get("/internal/health", headers=_auth(drain_secrets[1]))
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok" and body["last_drain_at"]


async def test_drain_failure_is_contained(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(internal_module, "drain", broken)
    with caplog.at_level(logging.ERROR, logger="app.internal"):
        await internal_module._run_drain()  # must not raise
    assert [r.getMessage() for r in caplog.records if r.name == "app.internal"] == ["drain_failed"]


# --------------------------------------------------------------------------- health


async def test_health_stale_when_never_drained(
    client: AsyncClient, drain_secrets: tuple[str, str]
) -> None:
    _assert_problem(
        await client.get("/internal/health", headers=_auth(drain_secrets[0])), 503, "drain-stale"
    )
    await _set_last_drain(None)
    _assert_problem(
        await client.get("/internal/health", headers=_auth(drain_secrets[0])), 503, "drain-stale"
    )


async def test_health_stale_when_old(client: AsyncClient, drain_secrets: tuple[str, str]) -> None:
    await _set_last_drain(datetime.now(UTC) - timedelta(minutes=16))
    _assert_problem(
        await client.get("/internal/health", headers=_auth(drain_secrets[0])), 503, "drain-stale"
    )


async def test_health_uses_configured_stale_window(
    client: AsyncClient, drain_secrets: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    await _set_last_drain(datetime.now(UTC) - timedelta(minutes=10))
    # Fresh under the default 15 minutes ...
    assert (await client.get("/internal/health", headers=_auth(drain_secrets[0]))).status_code == 200
    # ... stale once DRAIN_STALE_AFTER_MINUTES=5.
    monkeypatch.setenv("DRAIN_STALE_AFTER_MINUTES", "5")
    get_settings.cache_clear()
    _assert_problem(
        await client.get("/internal/health", headers=_auth(drain_secrets[0])), 503, "drain-stale"
    )


async def test_health_fresh(client: AsyncClient, drain_secrets: tuple[str, str]) -> None:
    at = datetime.now(UTC) - timedelta(minutes=4)
    await _set_last_drain(at)
    resp = await client.get("/internal/health", headers=_auth(drain_secrets[0]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert datetime.fromisoformat(body["last_drain_at"]) == at


def test_internal_routes_not_in_openapi(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/internal")]
    # The routes are still served (the 401s above prove routing; here just the schema).
