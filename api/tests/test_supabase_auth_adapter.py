"""SupabaseAuthAdmin against an httpx.MockTransport: URL, headers, 404 as success, error
translation, and the port wiring."""

import uuid
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.supabase_auth import SupabaseAuthAdmin, from_settings
from app.core.config import Settings
from app.main import create_app
from app.ports import auth_admin as auth_admin_port
from app.ports import file_store as file_store_port
from app.ports.auth_admin import AuthAdmin, AuthAdminError, LazyAuthAdmin, get_auth_admin
from app.ports.file_store import LazyFileStore
from tests.fakes.auth_admin import FakeAuthAdmin
from tests.fakes.file_store import FakeFileStore

BASE = "http://supabase.test/auth/v1"
KEY = "service-role-key-for-tests"
USER = uuid.UUID("0193a0a0-0000-7000-8000-000000000001")


def admin_with(responder) -> tuple[SupabaseAuthAdmin, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responder(request)

    return SupabaseAuthAdmin(BASE, KEY, transport=httpx.MockTransport(handler)), requests


def test_satisfies_the_port() -> None:
    admin, _ = admin_with(lambda r: httpx.Response(200))
    assert isinstance(admin, AuthAdmin)
    assert isinstance(FakeAuthAdmin(), AuthAdmin)


async def test_delete_user_calls_the_admin_endpoint_with_the_service_key() -> None:
    admin, requests = admin_with(lambda r: httpx.Response(200, json={}))
    await admin.delete_user(USER)
    (req,) = requests
    assert req.method == "DELETE"
    assert str(req.url) == f"{BASE}/admin/users/{USER}"
    assert req.headers["apikey"] == KEY
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(req.url)


async def test_404_means_already_deleted() -> None:
    admin, _ = admin_with(lambda r: httpx.Response(404, json={"msg": "User not found"}))
    await admin.delete_user(USER)  # no error


@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_error_status_becomes_auth_admin_error(status: int) -> None:
    admin, _ = admin_with(lambda r: httpx.Response(status, json={"msg": "secret detail"}))
    with pytest.raises(AuthAdminError, match=f"HTTP {status}") as info:
        await admin.delete_user(USER)
    assert "secret detail" not in str(info.value)


async def test_transport_error_becomes_auth_admin_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    admin, _ = admin_with(boom)
    with pytest.raises(AuthAdminError):
        await admin.delete_user(USER)


def test_requires_a_service_role_key() -> None:
    with pytest.raises(ValueError):
        SupabaseAuthAdmin(BASE, "")
    with pytest.raises(RuntimeError):
        from_settings(Settings(supabase_service_role_key=None))


async def test_from_settings_calls_supabase_url_auth_admin_users() -> None:
    """The base URL is built from SUPABASE_URL (not the token issuer):
    ``<SUPABASE_URL>/auth/v1/admin/users/{id}``; a trailing slash is tolerated."""
    for url in ("http://supabase.test", "http://supabase.test/"):
        admin = from_settings(Settings(supabase_url=url, supabase_service_role_key=SecretStr(KEY)))
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={})

        admin._transport = httpx.MockTransport(handler)
        await admin.delete_user(USER)
        assert [str(r.url) for r in requests] == [f"http://supabase.test/auth/v1/admin/users/{USER}"]


async def test_lazy_admin_builds_on_first_use_and_forwards() -> None:
    built: list[FakeAuthAdmin] = []

    def factory() -> FakeAuthAdmin:
        fake = FakeAuthAdmin(users={USER})
        built.append(fake)
        return fake

    lazy = LazyAuthAdmin(factory)
    assert built == []  # nothing built (no credentials needed) until first use
    await lazy.delete_user(USER)
    await lazy.delete_user(USER)
    assert len(built) == 1
    assert built[0].users == set() and built[0].deleted == [USER, USER]


async def test_lazy_file_store_forwards_delete_prefix_and_its_count() -> None:
    store = FakeFileStore()
    for path in (f"{USER}/resume/a", f"{USER}/document/b", "other/resume/c"):
        await store.put(path, b"x", "application/pdf")
    lazy = LazyFileStore(lambda: store)
    assert await lazy.delete_prefix(f"{USER}/") == 2
    assert store.paths_under(f"{USER}/") == [] and store.paths_under("other/") == ["other/resume/c"]
    assert await lazy.delete_prefix(f"{USER}/") == 0


@pytest.fixture
def restore_ports() -> Iterator[None]:
    """create_app installs process-wide ports; put back whatever was installed before."""
    saved_admin, saved_store = auth_admin_port._installed, file_store_port._installed
    try:
        yield
    finally:
        auth_admin_port._installed = saved_admin
        file_store_port._installed = saved_store


@pytest.mark.usefixtures("restore_ports")
def test_app_wiring_installs_a_lazy_admin_without_credentials() -> None:
    app = create_app()
    assert isinstance(app.state.auth_admin, LazyAuthAdmin)
    assert get_auth_admin() is app.state.auth_admin
    fake = FakeAuthAdmin()
    app = create_app(auth_admin=fake)
    assert app.state.auth_admin is fake and get_auth_admin() is fake
