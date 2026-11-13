"""I/O matrix: every error is application/problem+json from the one handler module."""

import json
import logging

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient
from pydantic import BaseModel, Field

from app.core.errors import DomainError

PROBLEM = "application/problem+json"
SECRET = "s3cr3t-db-password"


class Teapot(DomainError):
    status = 418
    slug = "teapot"
    title = "I'm a teapot"


class Body(BaseModel):
    name: str = Field(min_length=1)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    router = APIRouter(prefix="/api/v1/_test")

    @router.get("/validate")
    async def validate(n: int) -> dict[str, int]:
        return {"n": n}

    @router.post("/validate")
    async def validate_body(body: Body) -> dict[str, str]:
        return {"name": body.name}

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"connection failed for postgres://u:{SECRET}@db")

    @router.get("/domain")
    async def domain() -> None:
        raise Teapot("short and stout")

    app.include_router(router)
    return app


def _assert_problem(resp, status: int, slug: str) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith(PROBLEM)
    body = resp.json()
    assert body["type"] == f"/problems/{slug}"
    assert body["status"] == status
    for key in ("title", "detail", "instance"):
        assert isinstance(body[key], str) and body[key]
    return body


async def test_unknown_route_is_404_problem(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nope")
    body = _assert_problem(resp, 404, "not-found")
    assert body["instance"] == "/api/v1/nope"


async def test_bad_query_is_422_with_errors(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/_test/validate", params={"n": "x"})
    body = _assert_problem(resp, 422, "validation-error")
    assert body["errors"] and body["errors"][0]["loc"] == ["query", "n"]
    assert set(body["errors"][0]) == {"loc", "msg"}


async def test_bad_body_is_422_with_errors(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/_test/validate", json={"name": ""})
    body = _assert_problem(resp, 422, "validation-error")
    assert body["errors"][0]["loc"] == ["body", "name"]


async def test_unhandled_is_500_without_internals(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        resp = await client.get("/api/v1/_test/boom", headers={"x-request-id": "rid-500"})
    body = _assert_problem(resp, 500, "internal-error")
    raw = resp.text
    assert SECRET not in raw
    assert "Traceback" not in raw and "RuntimeError" not in raw
    assert body["detail"] == "An unexpected error occurred."
    records = [r for r in caplog.records if r.name == "app.errors"]
    assert records and records[0].exc_info is not None
    assert records[0].request_id == "rid-500"


async def test_unhandled_500_keeps_cors_and_request_id(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/_test/boom",
        headers={"Origin": "http://localhost:3000", "x-request-id": "rid-cors"},
    )
    _assert_problem(resp, 500, "internal-error")
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp.headers.get("x-request-id") == "rid-cors"


def test_unhandled_log_line_is_json() -> None:
    from app.core.logging import JsonFormatter

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "app.errors", logging.ERROR, __file__, 1, "unhandled_exception", None, sys.exc_info()
        )
    record.path = "/x"
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "unhandled_exception"
    assert line["level"] == "ERROR"
    assert line["exc_type"] == "RuntimeError"
    assert line["path"] == "/x"


async def test_domain_error_uses_declared_status_and_type(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/_test/domain")
    body = _assert_problem(resp, 418, "teapot")
    assert body["detail"] == "short and stout"
    assert body["title"] == "I'm a teapot"
