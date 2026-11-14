"""ApifyProviderRunner against an httpx.MockTransport: URL, headers, the documented status
mapping, token secrecy, and the port wiring."""

import json
import logging
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.apify import ApifyProviderRunner, from_settings
from app.core.config import Settings
from app.main import create_app
from app.ports import auth_admin as auth_admin_port
from app.ports import file_store as file_store_port
from app.ports import provider_runner as provider_runner_port
from app.ports.provider_runner import (
    LazyProviderRunner,
    ProviderAccount,
    ProviderKeyInvalid,
    ProviderRejected,
    ProviderRunner,
    ProviderUnavailable,
    RunState,
    get_provider_runner,
)
from tests.fakes.provider_runner import FakeProviderRunner

BASE = "https://apify.test"
TOKEN = "apify_api_SeCrEtToKeN1234567890abcdef"  # ggignore -- fake test token


def runner_with(responder) -> tuple[ApifyProviderRunner, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responder(request)

    runner = ApifyProviderRunner(BASE, "careerlaunch", transport=httpx.MockTransport(handler))
    return runner, requests


def test_satisfies_the_port() -> None:
    runner, _ = runner_with(lambda r: httpx.Response(200))
    assert isinstance(runner, ProviderRunner)
    assert isinstance(FakeProviderRunner(), ProviderRunner)


async def test_200_is_valid_and_sends_the_headers() -> None:
    runner, requests = runner_with(
        lambda r: httpx.Response(200, json={"data": {"id": "u123", "username": "me"}})
    )
    assert await runner.validate_token(SecretStr(TOKEN)) == ProviderAccount(user_id="u123")
    (req,) = requests
    assert req.method == "GET"
    assert str(req.url) == f"{BASE}/v2/users/me"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-apify-integration-platform"] == "careerlaunch"
    assert TOKEN not in str(req.url)


async def test_200_without_an_id_is_still_valid() -> None:
    runner, _ = runner_with(lambda r: httpx.Response(200, json={"data": {"username": "me"}}))
    assert await runner.validate_token(SecretStr(TOKEN)) == ProviderAccount(user_id=None)


async def test_401_is_invalid() -> None:
    body = {"error": {"type": "invalid-token", "message": "Authentication token is not valid."}}
    runner, _ = runner_with(lambda r: httpx.Response(401, json=body))
    with pytest.raises(ProviderKeyInvalid) as exc:
        await runner.validate_token(SecretStr(TOKEN))
    assert TOKEN not in str(exc.value)


async def test_403_insufficient_permissions_is_a_valid_scoped_token() -> None:
    body = {"error": {"type": "insufficient-permissions", "message": "nope"}}
    runner, _ = runner_with(lambda r: httpx.Response(403, json=body))
    assert await runner.validate_token(SecretStr(TOKEN)) == ProviderAccount(user_id=None)


@pytest.mark.parametrize("body", [{"error": {"type": "user-is-blocked"}}, {}, None])
async def test_other_403_is_invalid(body) -> None:
    runner, _ = runner_with(
        lambda r: httpx.Response(403, json=body) if body is not None else httpx.Response(403)
    )
    with pytest.raises(ProviderKeyInvalid):
        await runner.validate_token(SecretStr(TOKEN))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 404, 400])
async def test_error_status_is_unavailable(status: int) -> None:
    runner, _ = runner_with(lambda r: httpx.Response(status, json={"error": {"type": "x"}}))
    with pytest.raises(ProviderUnavailable) as exc:
        await runner.validate_token(SecretStr(TOKEN))
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>"),
        httpx.Response(200, json=[{"data": {"id": "u1"}}]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": None}),
        httpx.Response(200, json={"data": "u1"}),
        httpx.Response(200, json="ok"),
    ],
)
async def test_unusable_200_is_unavailable(response: httpx.Response) -> None:
    runner, _ = runner_with(lambda r: response)
    with pytest.raises(ProviderUnavailable):
        await runner.validate_token(SecretStr(TOKEN))


@pytest.mark.parametrize("user_id", [123, {"x": 1}, ["u"], "", "   ", None, True])
async def test_non_string_user_id_is_not_stored(user_id: object) -> None:
    runner, _ = runner_with(lambda r: httpx.Response(200, json={"data": {"id": user_id}}))
    assert await runner.validate_token(SecretStr(TOKEN)) == ProviderAccount(user_id=None)


@pytest.mark.parametrize("base", ["ftp://apify.test", "https://[invalid", "nonsense://x"])
async def test_unusable_base_url_is_unavailable(base: str) -> None:
    runner = ApifyProviderRunner(base, "careerlaunch")
    with pytest.raises(ProviderUnavailable) as exc:
        await runner.validate_token(SecretStr(TOKEN))
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("c")]
)
async def test_timeout_and_transport_errors_are_unavailable(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    runner, _ = runner_with(boom)
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderUnavailable) as exc:
        await runner.validate_token(SecretStr(TOKEN))
    assert exc.value.__cause__ is None
    assert TOKEN not in str(exc.value) and TOKEN not in caplog.text


async def test_token_never_in_repr_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    runner, _ = runner_with(lambda r: httpx.Response(500))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderUnavailable):
        await runner.validate_token(SecretStr(TOKEN))
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(runner)
    assert TOKEN not in repr(SecretStr(TOKEN))


async def test_requires_a_secret_str() -> None:
    runner, _ = runner_with(lambda r: httpx.Response(200))
    with pytest.raises(TypeError):
        await runner.validate_token(TOKEN)  # type: ignore[arg-type]


def test_from_settings_uses_the_configured_base_and_platform() -> None:
    runner = from_settings(
        Settings(apify_api_base="https://api.apify.example/", apify_integration_platform="cl-x")
    )
    assert runner._base == "https://api.apify.example"
    assert runner._platform == "cl-x"
    assert Settings().apify_api_base == "https://api.apify.com"


@pytest.fixture
def restore_ports() -> Iterator[None]:
    from app.ports import source_adapter as source_adapter_port

    saved = (
        auth_admin_port._installed,
        file_store_port._installed,
        provider_runner_port._installed,
        source_adapter_port._provider_source,
    )
    try:
        yield
    finally:
        (
            auth_admin_port._installed,
            file_store_port._installed,
            provider_runner_port._installed,
            source_adapter_port._provider_source,
        ) = saved


@pytest.mark.usefixtures("restore_ports")
def test_app_wiring_installs_a_lazy_runner_and_accepts_a_fake() -> None:
    app = create_app()
    assert isinstance(app.state.provider_runner, LazyProviderRunner)
    assert get_provider_runner() is app.state.provider_runner
    fake = FakeProviderRunner()
    app = create_app(provider_runner=fake)
    assert app.state.provider_runner is fake and get_provider_runner() is fake


# --------------------------------------------------------------------------- runs (Story 2.3)

RUN_BODY = {
    "data": {
        "id": "HG7ML7M8z78YcAPEB",
        "status": "RUNNING",
        "defaultDatasetId": "WkzbQMuFYuamGv3YF",
        "usageTotalUsd": 0.0123,
    }
}


async def test_start_run_request() -> None:
    runner, requests = runner_with(lambda r: httpx.Response(201, json=RUN_BODY))
    run_input = {"urls": ["https://www.linkedin.com/jobs/search/?keywords=x"], "limitPerSource": 5}
    run_id = await runner.start_run(
        SecretStr(TOKEN),
        "curious_coder/linkedin-jobs-scraper",
        run_input,
        max_items=100,
        max_charge_usd=0.25,
    )
    assert run_id == "HG7ML7M8z78YcAPEB"
    (req,) = requests
    assert req.method == "POST"
    assert req.url.path == "/v2/acts/curious_coder~linkedin-jobs-scraper/runs"
    assert dict(req.url.params) == {"maxItems": "100", "maxTotalChargeUsd": "0.25"}
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-apify-integration-platform"] == "careerlaunch"
    assert json.loads(req.content) == run_input
    assert TOKEN not in str(req.url) and TOKEN.encode() not in req.content
    body = req.content.lower()
    for word in (b"cookie", b"li_at", b"login", b"password"):
        assert word not in body


async def test_start_run_rejects_bad_arguments() -> None:
    runner, requests = runner_with(lambda r: httpx.Response(201, json=RUN_BODY))
    for actor in ("no-slash", "a/b/c", "../x/y"):
        with pytest.raises(ValueError):
            await runner.start_run(SecretStr(TOKEN), actor, {}, max_items=1, max_charge_usd=1)
    with pytest.raises(ValueError):
        await runner.start_run(SecretStr(TOKEN), "a/b", {}, max_items=0, max_charge_usd=1)
    with pytest.raises(ValueError):
        await runner.start_run(SecretStr(TOKEN), "a/b", {}, max_items=1, max_charge_usd=0)
    with pytest.raises(TypeError):
        await runner.start_run(TOKEN, "a/b", {}, max_items=1, max_charge_usd=1)  # type: ignore[arg-type]
    assert requests == []


async def test_max_charge_is_a_fixed_decimal() -> None:
    runner, requests = runner_with(lambda r: httpx.Response(201, json=RUN_BODY))
    await runner.start_run(SecretStr(TOKEN), "a/b", {}, max_items=1, max_charge_usd=0.00001)
    assert requests[0].url.params["maxTotalChargeUsd"] == "0.00001"


async def test_abort_run() -> None:
    runner, requests = runner_with(lambda r: httpx.Response(200, json=RUN_BODY))
    assert await runner.abort_run(SecretStr(TOKEN), "HG7ML7M8z78YcAPEB") is None
    (req,) = requests
    assert req.method == "POST" and req.url.path == "/v2/actor-runs/HG7ML7M8z78YcAPEB/abort"
    assert req.headers["x-apify-integration-platform"] == "careerlaunch"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
async def test_abort_run_swallows_errors(status: int, caplog: pytest.LogCaptureFixture) -> None:
    runner, _ = runner_with(lambda r: httpx.Response(status, json={"error": {"type": "x"}}))
    with caplog.at_level(logging.DEBUG):
        await runner.abort_run(SecretStr(TOKEN), "run1")  # never raises
    assert TOKEN not in caplog.text

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c")

    runner, _ = runner_with(boom)
    await runner.abort_run(SecretStr(TOKEN), "run1")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ProviderKeyInvalid),
        (402, ProviderRejected),
        (403, ProviderRejected),
        (429, ProviderUnavailable),
        (500, ProviderUnavailable),
        (404, ProviderUnavailable),
    ],
)
async def test_run_call_error_mapping(status: int, error: type[Exception]) -> None:
    body = {"error": {"type": "not-enough-usage-to-run-paid-actor", "message": "m"}}
    runner, _ = runner_with(lambda r: httpx.Response(status, json=body))
    for call in (
        runner.start_run(SecretStr(TOKEN), "a/b", {}, max_items=1, max_charge_usd=1),
        runner.get_run(SecretStr(TOKEN), "run1"),
        runner.fetch_items(SecretStr(TOKEN), "ds1", limit=5),
    ):
        with pytest.raises(error) as exc:
            await call
        assert TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(201, content=b"<html>"),
        httpx.Response(201, json={"data": {}}),
        httpx.Response(201, json={"data": {"id": "has space"}}),
        httpx.Response(201, json=[]),
    ],
)
async def test_start_run_without_a_run_id_is_unavailable(response: httpx.Response) -> None:
    runner, _ = runner_with(lambda r: response)
    with pytest.raises(ProviderUnavailable):
        await runner.start_run(SecretStr(TOKEN), "a/b", {}, max_items=1, max_charge_usd=1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("READY", "READY"),
        ("RUNNING", "RUNNING"),
        ("TIMING-OUT", "RUNNING"),
        ("ABORTING", "RUNNING"),
        ("SUCCEEDED", "SUCCEEDED"),
        ("FAILED", "FAILED"),
        ("TIMED-OUT", "TIMED-OUT"),
        ("ABORTED", "ABORTED"),
    ],
)
async def test_get_run(raw: str, expected: str) -> None:
    body = {"data": {**RUN_BODY["data"], "status": raw}}
    runner, requests = runner_with(lambda r: httpx.Response(200, json=body))
    state = await runner.get_run(SecretStr(TOKEN), "HG7ML7M8z78YcAPEB")
    assert state == RunState(expected, 0.0123, "WkzbQMuFYuamGv3YF")  # type: ignore[arg-type]
    (req,) = requests
    assert req.method == "GET" and req.url.path == "/v2/actor-runs/HG7ML7M8z78YcAPEB"
    assert req.headers["x-apify-integration-platform"] == "careerlaunch"


async def test_get_run_unknown_status_or_missing_fields() -> None:
    runner, _ = runner_with(lambda r: httpx.Response(200, json={"data": {"status": "WEIRD"}}))
    with pytest.raises(ProviderUnavailable):
        await runner.get_run(SecretStr(TOKEN), "run1")
    runner, _ = runner_with(
        lambda r: httpx.Response(200, json={"data": {"status": "SUCCEEDED", "usageTotalUsd": "x"}})
    )
    assert await runner.get_run(SecretStr(TOKEN), "run1") == RunState("SUCCEEDED", None, None)
    with pytest.raises(ValueError):
        await runner.get_run(SecretStr(TOKEN), "../users/me")


async def test_fetch_items() -> None:
    items = [{"id": "1"}, {"id": "2"}, "junk", {"id": "3"}]
    runner, requests = runner_with(lambda r: httpx.Response(200, json=items))
    got = await runner.fetch_items(SecretStr(TOKEN), "WkzbQMuFYuamGv3YF", limit=3)
    assert got == [{"id": "1"}, {"id": "2"}]
    (req,) = requests
    assert req.url.path == "/v2/datasets/WkzbQMuFYuamGv3YF/items"
    assert dict(req.url.params) == {"format": "json", "clean": "true", "limit": "3"}
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    runner, _ = runner_with(lambda r: httpx.Response(200, json={"items": []}))
    with pytest.raises(ProviderUnavailable):
        await runner.fetch_items(SecretStr(TOKEN), "ds1", limit=3)


async def test_lazy_runner_delegates_runs() -> None:
    fake = FakeProviderRunner()
    lazy = LazyProviderRunner(lambda: fake)
    run_id = await lazy.start_run(SecretStr(TOKEN), "a/b", {"x": 1}, max_items=2, max_charge_usd=0.1)
    assert run_id == "run1" and fake.started[0].max_items == 2
    assert (await lazy.get_run(SecretStr(TOKEN), run_id)).status == "SUCCEEDED"
    assert await lazy.fetch_items(SecretStr(TOKEN), "ds1", limit=1) == []
    await lazy.abort_run(SecretStr(TOKEN), run_id)
    assert fake.aborted == [(TOKEN, run_id)]


@pytest.mark.usefixtures("restore_ports")
def test_app_wiring_installs_the_linkedin_source() -> None:
    from app.adapters.apify import ApifyLinkedInSource
    from app.ports.source_adapter import get_provider_source

    app = create_app()
    assert isinstance(get_provider_source(), ApifyLinkedInSource)
    assert app.state.provider_source is get_provider_source()


@pytest.mark.usefixtures("restore_ports")
def test_unsupported_allowlisted_actor_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv(
        "APIFY_ACTOR_ALLOWLIST", "curious_coder/linkedin-jobs-scraper,someone/other-actor"
    )
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="someone/other-actor"):
            create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
