from fastapi import APIRouter, FastAPI
from httpx import AsyncClient

from app.main import include_internal_router


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers["x-request-id"]


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health", headers={"x-request-id": "abc123"})
    assert resp.headers["x-request-id"] == "abc123"


async def test_cors_allows_localhost_only(client: AsyncClient) -> None:
    ok = await client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = await client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


async def test_internal_routes_excluded_from_openapi(app: FastAPI, client: AsyncClient) -> None:
    internal = APIRouter()

    @internal.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    include_internal_router(app, internal)
    app.openapi_schema = None
    paths = app.openapi()["paths"]
    assert "/api/v1/health" in paths
    assert not [p for p in paths if p.startswith("/internal")]
    served = await client.get("/internal/ping")
    assert served.status_code == 200
