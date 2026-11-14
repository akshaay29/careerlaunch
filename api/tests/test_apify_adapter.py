"""ApifyProviderRunner against an httpx.MockTransport: URL, headers, the documented status
mapping, token secrecy, and the port wiring."""

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
    ProviderRunner,
    ProviderUnavailable,
    get_provider_runner,
)
from tests.fakes.provider_runner import FakeProviderRunner

BASE = "https://apify.test"
TOKEN = "apify_api_SeCrEtToKeN1234567890abcdef"


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
    saved = (
        auth_admin_port._installed,
        file_store_port._installed,
        provider_runner_port._installed,
    )
    try:
        yield
    finally:
        (
            auth_admin_port._installed,
            file_store_port._installed,
            provider_runner_port._installed,
        ) = saved


@pytest.mark.usefixtures("restore_ports")
def test_app_wiring_installs_a_lazy_runner_and_accepts_a_fake() -> None:
    app = create_app()
    assert isinstance(app.state.provider_runner, LazyProviderRunner)
    assert get_provider_runner() is app.state.provider_runner
    fake = FakeProviderRunner()
    app = create_app(provider_runner=fake)
    assert app.state.provider_runner is fake and get_provider_runner() is fake
