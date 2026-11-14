"""SupabaseStorageFileStore against an httpx.MockTransport: URLs, headers, the download
parameter, idempotent delete, error translation and ensure_bucket."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.supabase_storage import SupabaseStorageFileStore, from_settings
from app.core.config import Settings
from app.ports.file_store import FileStore, FileStoreError

BASE = "http://supabase.test/storage/v1"
KEY = "service-role-key-for-tests"
PATH = "0193a0a0-0000-7000-8000-000000000001/resume/0193a0a0-0000-7000-8000-000000000002"


class Recorder:
    def __init__(self, responder) -> None:
        self.requests: list[httpx.Request] = []
        self.responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)


def store_with(responder) -> tuple[SupabaseStorageFileStore, Recorder]:
    recorder = Recorder(responder)
    return (
        SupabaseStorageFileStore(BASE, KEY, "private", transport=httpx.MockTransport(recorder)),
        recorder,
    )


def assert_auth(request: httpx.Request) -> None:
    assert request.headers["apikey"] == KEY
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)


def test_satisfies_the_port() -> None:
    store, _ = store_with(lambda r: httpx.Response(200))
    assert isinstance(store, FileStore)


async def test_put_posts_bytes_to_the_object_path() -> None:
    store, rec = store_with(lambda r: httpx.Response(200, json={"Key": f"private/{PATH}"}))
    await store.put(PATH, b"%PDF-1.7 data", "application/pdf")
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == f"{BASE}/object/private/{PATH}"
    assert req.content == b"%PDF-1.7 data"
    assert req.headers["content-type"] == "application/pdf"
    assert req.headers["x-upsert"] == "true"
    assert_auth(req)


async def test_put_error_becomes_file_store_error() -> None:
    store, _ = store_with(lambda r: httpx.Response(413, json={"error": "Payload too large"}))
    with pytest.raises(FileStoreError, match="HTTP 413"):
        await store.put(PATH, b"x", "application/pdf")


async def test_transport_error_becomes_file_store_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    store, _ = store_with(boom)
    with pytest.raises(FileStoreError):
        await store.put(PATH, b"x", "application/pdf")


@pytest.mark.parametrize("bad", ["", "/abs/path", "a/../b"])
async def test_rejects_unsafe_paths(bad: str) -> None:
    store, rec = store_with(lambda r: httpx.Response(200))
    with pytest.raises(ValueError):
        await store.put(bad, b"x", "application/pdf")
    assert rec.requests == []


async def test_delete_sends_prefixes_and_is_idempotent() -> None:
    # Storage answers 200 with only the objects it removed: [] when nothing was there.
    store, rec = store_with(lambda r: httpx.Response(200, json=[]))
    await store.delete([PATH])
    await store.delete([PATH])
    assert len(rec.requests) == 2
    req = rec.requests[0]
    assert req.method == "DELETE"
    assert str(req.url) == f"{BASE}/object/private"
    assert json.loads(req.content) == {"prefixes": [PATH]}
    assert_auth(req)


async def test_delete_of_nothing_makes_no_request() -> None:
    store, rec = store_with(lambda r: httpx.Response(200, json=[]))
    await store.delete([])
    assert rec.requests == []


async def test_delete_404_is_not_an_error_but_500_is() -> None:
    store, _ = store_with(lambda r: httpx.Response(404, json={"error": "not_found"}))
    await store.delete([PATH])
    store, _ = store_with(lambda r: httpx.Response(500))
    with pytest.raises(FileStoreError):
        await store.delete([PATH])


async def test_get_downloads_the_object_with_the_service_key() -> None:
    store, rec = store_with(lambda r: httpx.Response(200, content=b"%PDF-1.7 bytes"))
    assert await store.get(PATH) == b"%PDF-1.7 bytes"
    (req,) = rec.requests
    assert req.method == "GET"
    assert str(req.url) == f"{BASE}/object/authenticated/private/{PATH}"
    assert_auth(req)


@pytest.mark.parametrize("status", [400, 404, 500])
async def test_get_error_becomes_file_store_error(status: int) -> None:
    store, _ = store_with(lambda r: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(FileStoreError, match=f"HTTP {status}"):
        await store.get(PATH)


async def test_get_transport_error_and_unsafe_path() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    store, _ = store_with(boom)
    with pytest.raises(FileStoreError):
        await store.get(PATH)
    store, rec = store_with(lambda r: httpx.Response(200))
    with pytest.raises(ValueError):
        await store.get("a/../b")
    assert rec.requests == []


async def test_signed_url_is_short_lived_attachment() -> None:
    token_path = f"/object/sign/private/{PATH}?token=abc.def"
    store, rec = store_with(lambda r: httpx.Response(200, json={"signedURL": token_path}))
    url = await store.signed_download_url(PATH, 300, "My CV (final).pdf")
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == f"{BASE}/object/sign/private/{PATH}"
    assert json.loads(req.content) == {"expiresIn": 300}
    assert_auth(req)

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{BASE}/object/sign/private/{PATH}"
    query = parse_qs(parts.query)
    assert query["token"] == ["abc.def"]
    assert query["download"] == ["My CV (final).pdf"]
    assert "%20" in url and " " not in url  # the name is URL-encoded
    assert KEY not in url


async def test_signed_url_accepts_absolute_url_without_query() -> None:
    store, _ = store_with(
        lambda r: httpx.Response(200, json={"signedURL": "https://cdn.test/x/y"})
    )
    assert await store.signed_download_url(PATH, 60, "a&b.pdf") == "https://cdn.test/x/y?download=a%26b.pdf"


async def test_signed_url_missing_object_is_an_error() -> None:
    store, _ = store_with(lambda r: httpx.Response(400, json={"statusCode": "404"}))
    with pytest.raises(FileStoreError):
        await store.signed_download_url(PATH, 300, "cv.pdf")


TYPES = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
]
WANT = {"public": False, "file_size_limit": 5242880, "allowed_mime_types": sorted(TYPES)}


async def test_ensure_bucket_creates_private_bucket_when_missing() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found"})
        return httpx.Response(200, json={"name": "private"})

    store, rec = store_with(respond)
    assert await store.ensure_bucket(file_size_limit=5242880, allowed_mime_types=TYPES) == "created"
    get, post = rec.requests
    assert str(get.url) == f"{BASE}/bucket/private"
    assert post.method == "POST" and str(post.url) == f"{BASE}/bucket"
    assert json.loads(post.content) == {"id": "private", "name": "private", **WANT}


async def test_ensure_bucket_leaves_a_correct_bucket_alone() -> None:
    store, rec = store_with(
        lambda r: httpx.Response(
            200, json={"id": "private", **WANT, "allowed_mime_types": TYPES}  # any order
        )
    )
    assert await store.ensure_bucket(file_size_limit=5242880, allowed_mime_types=TYPES) == "unchanged"
    assert [r.method for r in rec.requests] == ["GET"]


@pytest.mark.parametrize(
    "current",
    [
        {"public": True, "file_size_limit": None, "allowed_mime_types": None},
        {"public": False, "file_size_limit": 5242880, "allowed_mime_types": None},
        {"public": False, "file_size_limit": 5242880, "allowed_mime_types": ["application/pdf"]},
    ],
    ids=["public", "any-type", "pdf-only"],
)
async def test_ensure_bucket_fixes_a_bucket_that_differs(current: dict) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "private", **current})
        return httpx.Response(200, json={"message": "Successfully updated"})

    store, rec = store_with(respond)
    assert await store.ensure_bucket(file_size_limit=5242880, allowed_mime_types=TYPES) == "updated"
    put = rec.requests[1]
    assert put.method == "PUT" and str(put.url) == f"{BASE}/bucket/private"
    assert json.loads(put.content) == WANT


@pytest.mark.parametrize(
    "body",
    [{"json": ["not", "an", "object"]}, {"json": "a string"}, {"content": b"<html>no</html>"}],
    ids=["list", "string", "not-json"],
)
async def test_malformed_success_bodies_become_file_store_errors(body: dict) -> None:
    store, _ = store_with(lambda r: httpx.Response(200, **body))
    with pytest.raises(FileStoreError, match="invalid response"):
        await store.signed_download_url(PATH, 300, "cv.pdf")
    with pytest.raises(FileStoreError, match="invalid response"):
        await store.ensure_bucket(file_size_limit=5242880, allowed_mime_types=TYPES)


def test_from_settings_requires_the_service_role_key() -> None:
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        from_settings(Settings(supabase_service_role_key=None))
    store = from_settings(
        Settings(supabase_url="http://supabase.test/", supabase_service_role_key=SecretStr(KEY))
    )
    assert isinstance(store, SupabaseStorageFileStore)
