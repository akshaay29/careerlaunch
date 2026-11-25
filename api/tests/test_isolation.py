"""AD-5 / NFR-8: every Student-owned endpoint is isolated per Student (Story 4.4).

The routes are ENUMERATED from the app, so the guarantee keeps up as the API grows:

* Every route with a path parameter must be in :data:`REGISTRY` (Student B gets 404 for
  Student A's id) or in :data:`EXEMPT` (with a reason); the completeness guard fails naming any
  route that is in neither.
* Every GET route without a path parameter under ``/api/v1`` (the list and singleton routes)
  is called as Student B and must return nothing of Student A's.
* Every other Student route without a path parameter (a write on the caller's own rows) must
  be classified in :data:`WRITE_ROUTES`; ids taken in a body are checked like path ids.
* Every route that requires the admin role (by dependency or under ``/api/v1/admin``) must be
  in :data:`ADMIN_ROUTES` and returns 403 to a signed-in Student.

Read-only routes over shared pool data (``GET /listings/{listing_id}`` and its match routes)
are marked ``shared``: a pool Listing is readable by every Student by design, so the check uses
A's PRIVATE manual Listing instead.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient

from app.core.auth import require_admin
from app.main import API_ADMIN, API_PUBLIC, API_V1, create_app
from app.modules.profile.service import document_path, resume_path
from app.modules.tracker import router as tracker_router
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.file_store import FakeFileStore
from tests.maintenance_fixtures import MaintenanceDb, mdb  # noqa: F401 (fixture)
from tests.tracker_fixtures import Student, TrackerDb, make_student

pytestmark = pytest.mark.usefixtures("clean_jobs")

PROBLEM = "application/problem+json"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000001"
IGNORED_METHODS = {"HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Entry:
    """How to call one id route with Student A's resource as Student B.

    ``resource`` names the resource of A's whose id fills the path parameter. ``json`` is a
    valid body (so the 404 is not hidden behind a 422). ``shared`` explains why the route reads
    shared pool data (A's private Listing is used). ``scoped`` explains a route that never
    reads the id's resource, only the caller's own rows keyed by it: it answers 200 and must
    return nothing of A's instead of 404.
    """

    resource: str
    json: dict[str, Any] | None = None
    shared: str | None = None
    scoped: str | None = None


_SHARED_LISTING = (
    "pool Listings are shared: every Student may read them; A's private manual Listing is used"
)

REGISTRY: dict[tuple[str, str], Entry] = {
    # profile: Resume versions and Documents
    ("POST", "/api/v1/resumes/{resume_id}/activate"): Entry("resume"),
    ("GET", "/api/v1/resumes/{resume_id}/download"): Entry("resume"),
    ("DELETE", "/api/v1/resumes/{resume_id}"): Entry("resume"),
    ("GET", "/api/v1/documents/{document_id}/download"): Entry("document"),
    ("DELETE", "/api/v1/documents/{document_id}"): Entry("document"),
    # listings / matching: shared pool data, checked with A's private Listing
    ("GET", "/api/v1/listings/{listing_id}"): Entry("listing", shared=_SHARED_LISTING),
    ("GET", "/api/v1/listings/{listing_id}/match"): Entry("listing", shared=_SHARED_LISTING),
    ("POST", "/api/v1/listings/{listing_id}/match/viewed"): Entry(
        "listing", shared=_SHARED_LISTING
    ),
    # tracker: Applications
    ("GET", "/api/v1/applications/by-listing/{listing_id}"): Entry(
        "listing",
        scoped="a lookup over the caller's own Applications keyed by a Listing id: it always "
        "answers 200 with the caller's own Application id or null, never A's",
    ),
    ("GET", "/api/v1/applications/{application_id}"): Entry("application"),
    ("PATCH", "/api/v1/applications/{application_id}"): Entry(
        "application", json={"notes": "B was here"}
    ),
    ("POST", "/api/v1/applications/{application_id}/status"): Entry(
        "application", json={"status": "applied"}
    ),
    ("DELETE", "/api/v1/applications/{application_id}"): Entry("application"),
    ("GET", "/api/v1/applications/{application_id}/history"): Entry("application"),
}

# Routes with a path parameter that is not a resource id. Every entry carries a reason.
EXEMPT: dict[tuple[str, str], str] = {
    ("PUT", "/api/v1/consents/{purpose}"): (
        "{purpose} is a fixed consent purpose (an enum), not a resource id: the caller always "
        "grants their own consent"
    ),
    ("DELETE", "/api/v1/consents/{purpose}"): (
        "{purpose} is a fixed consent purpose (an enum), not a resource id: the caller always "
        "revokes their own consent"
    ),
}

# Student routes that change state and take no path parameter. Each acts only on the
# caller's own rows (``owner_id`` from the token); one taking an id in its BODY is checked in
# ``test_b_cannot_save_or_attach_as_resources_by_body_id``. The guard below fails on any such
# route missing here, so a new one gets a look.
_OWN = "acts only on the caller's own rows; no other Student's id is accepted"
WRITE_ROUTES: dict[tuple[str, str], str] = {
    ("DELETE", "/api/v1/me"): _OWN,
    ("PUT", "/api/v1/provider-key"): _OWN,
    ("DELETE", "/api/v1/provider-key"): _OWN,
    ("PUT", "/api/v1/spend-caps"): _OWN,
    ("POST", "/api/v1/resumes"): _OWN,
    ("POST", "/api/v1/documents"): _OWN,
    ("PATCH", "/api/v1/skill-profile"): _OWN,
    ("POST", "/api/v1/skill-profile/reset"): _OWN,
    ("PUT", "/api/v1/preferences"): _OWN,
    ("POST", "/api/v1/applications"): (
        "body listing_id: A's private Listing is a 404 to B (body-id test)"
    ),
    ("POST", "/api/v1/applications/manual"): _OWN,
}

# Every route that requires the admin role.
ADMIN_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/admin/me"),
}

# Query parameters a GET route without path parameters needs to answer 200. ``{listing}``
# is replaced by A's private Listing id (B asking about it must learn nothing).
LIST_QUERY: dict[str, dict[str, str]] = {
    "/api/v1/listings/search": {"q": "Isolation Probe"},
    "/api/v1/applications/by-listings": {"ids": "{listing}"},
    "/api/v1/skills": {"q": "py"},
}

# Positive controls: A sees their own resource on these list routes, so B seeing nothing of
# A's there is meaningful.
LIST_OWN: dict[str, str] = {
    "/api/v1/resumes": "resume",
    "/api/v1/documents": "document",
    "/api/v1/applications": "application",
    "/api/v1/listings/search": "listing",
    "/api/v1/applications/by-listings": "application",
}


# --------------------------------------------------------------------------- enumeration


@dataclass(frozen=True)
class RouteInfo:
    method: str
    path: str
    name: str
    admin: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def has_param(self) -> bool:
        return "{" in self.path

    def __str__(self) -> str:
        return f"{self.method} {self.path} ({self.name})"


def _dependency_calls(dependant: Any) -> list[Callable[..., Any]]:
    calls: list[Callable[..., Any]] = []
    for sub in dependant.dependencies:
        if sub.call is not None:
            calls.append(sub.call)
        calls.extend(_dependency_calls(sub))
    return calls


def enumerate_routes(app: Any) -> list[RouteInfo]:
    """Every (method, path) the app serves, with whether it requires the admin role."""
    routes: list[RouteInfo] = []
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue  # /openapi.json and the docs pages
        admin = require_admin in _dependency_calls(context.dependant)
        for method in sorted((context.methods or set()) - IGNORED_METHODS):
            routes.append(RouteInfo(method, context.path, context.name, admin))
    return routes


ROUTES = enumerate_routes(create_app())


def _student_route(route: RouteInfo) -> bool:
    return route.path.startswith(f"{API_V1}/") and not route.path.startswith(
        (f"{API_ADMIN}/", f"{API_PUBLIC}/")
    )


def _admin_route(route: RouteInfo) -> bool:
    return route.admin or route.path == API_ADMIN or route.path.startswith(f"{API_ADMIN}/")


# --------------------------------------------------------------------------- completeness guards


def test_the_enumeration_finds_the_known_routes() -> None:
    keys = {r.key for r in ROUTES}
    assert ("GET", "/api/v1/applications/{application_id}") in keys
    assert ("GET", "/api/v1/admin/me") in keys
    assert len([r for r in ROUTES if r.has_param]) >= len(REGISTRY)


def unregistered(routes: list[RouteInfo]) -> list[str]:
    """Routes with a path parameter that are neither registered nor exempt."""
    return [
        str(r) for r in routes if r.has_param and r.key not in REGISTRY and r.key not in EXEMPT
    ]


def test_every_id_route_is_registered_or_exempt() -> None:
    missing = unregistered(ROUTES)
    assert not missing, (
        "routes with a path parameter need an isolation REGISTRY entry (or an EXEMPT reason) "
        "in tests/test_isolation.py: " + "; ".join(missing)
    )


def test_the_guard_names_a_new_unregistered_id_route() -> None:
    from fastapi import APIRouter

    from app.main import include_api_router

    app = create_app()
    extra = APIRouter()

    @extra.get("/widgets/{widget_id}")
    async def get_widget(widget_id: uuid.UUID) -> dict[str, str]:
        return {}

    include_api_router(app, extra)
    assert unregistered(enumerate_routes(app)) == [
        "GET /api/v1/widgets/{widget_id} (get_widget)"
    ]


def test_registry_and_exemptions_name_only_existing_routes() -> None:
    keys = {r.key for r in ROUTES}
    stale = [f"{m} {p}" for m, p in [*REGISTRY, *EXEMPT] if (m, p) not in keys]
    assert not stale, "isolation entries for routes that no longer exist: " + "; ".join(stale)
    assert not set(REGISTRY) & set(EXEMPT)


def test_every_exemption_and_special_case_has_a_reason() -> None:
    assert all(reason.strip() for reason in EXEMPT.values())
    for key, entry in REGISTRY.items():
        assert key[1].count("{") == 1, f"one path parameter expected: {key}"
        assert entry.resource in {"resume", "document", "listing", "application"}, key
        assert not (entry.shared and entry.scoped), key
        if entry.shared is not None:
            assert entry.shared.strip() and entry.resource == "listing", key
        if entry.scoped is not None:
            assert entry.scoped.strip(), key


def test_every_student_write_route_without_an_id_is_classified() -> None:
    writes = {
        r.key: r
        for r in ROUTES
        if r.method != "GET" and not r.has_param and _student_route(r)
    }
    missing = [str(writes[k]) for k in writes if k not in WRITE_ROUTES]
    assert not missing, (
        "Student write routes need a WRITE_ROUTES entry in tests/test_isolation.py: "
        + "; ".join(missing)
    )
    stale = [f"{m} {p}" for m, p in WRITE_ROUTES if (m, p) not in writes]
    assert not stale, "WRITE_ROUTES entries that no longer exist: " + "; ".join(stale)
    assert all(reason.strip() for reason in WRITE_ROUTES.values())


def test_every_admin_route_is_listed() -> None:
    found = {r.key: r for r in ROUTES if _admin_route(r)}
    missing = [str(found[k]) for k in found if k not in ADMIN_ROUTES]
    assert not missing, (
        "admin routes need an ADMIN_ROUTES entry in tests/test_isolation.py: " + "; ".join(missing)
    )
    stale = [f"{m} {p}" for m, p in ADMIN_ROUTES if (m, p) not in found]
    assert not stale, "ADMIN_ROUTES entries that are not admin routes: " + "; ".join(stale)
    # Every route under /api/v1/admin requires the role (not only the path prefix).
    unguarded = [str(r) for r in found.values() if not r.admin]
    assert not unguarded, "admin-path routes without require_admin: " + "; ".join(unguarded)


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def store() -> FakeFileStore:
    return FakeFileStore()


@pytest.fixture
def app(store: FakeFileStore):
    """Overrides conftest's ``app``: wired with the fake FileStore (no real Storage)."""
    return create_app(file_store=store)


@pytest.fixture
def no_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skipped() -> None:
        return None

    monkeypatch.setattr(tracker_router, "drain_priority", skipped)


@dataclass
class World:
    a: Student
    b: Student
    ids: dict[str, str]  # A's resources by name

    def forbidden(self) -> dict[str, str]:
        """Everything of A's that must never reach B: the ids (A's account id included) and
        A's distinctive values (title, filenames, notes, description, stored score)."""
        return {**self.ids, "account": str(self.a[0]), **A_TEXTS}


# A's distinctive strings and values (low-entropy, found nowhere else in the fixtures).
A_TITLE = "Isolation Probe Intern"
A_RESUME_FILE = "isolation-a-cv.pdf"
A_DOCUMENT_FILE = "isolation-a-letter.pdf"
A_NOTES = "isolation-a-notes"
A_DESCRIPTION = "isolation-a-description with Python and SQL."
A_SCORE = 73
# A valid stored breakdown whose points sum to A_SCORE, so A_SCORE is served as stored.
A_BREAKDOWN = {
    "algo_version": 1,
    "weights_version": 1,
    "vocab_version": "v1",
    "matched_skills": [],
    "missing_skills": [],
    "components": [
        {"name": "skills", "points": 50, "max_points": 70},
        {"name": "text", "points": A_SCORE - 50, "max_points": 30},
    ],
}
A_TEXTS = {
    "title": A_TITLE,
    "resume filename": A_RESUME_FILE,
    "document filename": A_DOCUMENT_FILE,
    "notes": A_NOTES,
    "description": "isolation-a-description",
    # Compact JSON, as the API sends it: A's stored score of A's private Listing.
    "score": f'"score":{A_SCORE}',
}


@pytest.fixture
async def world(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    mdb: MaintenanceDb,  # noqa: F811 (Listings start and end empty)
    migrated_db: str,
    no_drain: None,
    store: FakeFileStore,
) -> World:
    a = make_student(accounts, tokens)
    b = make_student(accounts, tokens)
    owner = a[0]
    db = TrackerDb(migrated_db)

    resume_id = uuid.uuid7()
    db.execute(
        "INSERT INTO app.resumes (id, owner_id, filename, content_type, size_bytes, sha256) "
        "VALUES (%s, %s, %s, 'application/pdf', 10, repeat('0', 64))",
        (resume_id, owner, A_RESUME_FILE),
    )
    document_id = uuid.uuid7()
    db.execute(
        "INSERT INTO app.documents (id, owner_id, filename, content_type, size_bytes, sha256) "
        "VALUES (%s, %s, %s, 'application/pdf', 10, repeat('0', 64))",
        (document_id, owner, A_DOCUMENT_FILE),
    )
    await store.put(resume_path(owner, resume_id), b"%PDF-1.7 A", "application/pdf")
    await store.put(document_path(owner, document_id), b"%PDF-1.7 A", "application/pdf")
    added = await client.post(
        f"{API_V1}/applications/manual",
        json={
            "url": "https://jobs.example.com/isolation-probe",
            "title": A_TITLE,
            "company": "Example Labs",
            "description": A_DESCRIPTION,
        },
        headers=a[1],
    )
    assert added.status_code == 201, added.text
    application_id = added.json()["id"]
    listing_id = added.json()["listing_id"]
    noted = await client.patch(
        f"{API_V1}/applications/{application_id}", json={"notes": A_NOTES}, headers=a[1]
    )
    assert noted.status_code == 200, noted.text
    # A stored Match Score of A's private Listing, so the score routes have something to leak.
    db.execute(
        "INSERT INTO app.match_scores (owner_id, listing_id, score, breakdown, algo_version, "
        "weights_version, vocab_version, computed_at) "
        "VALUES (%s, %s, %s, %s::jsonb, 1, 1, 'v1', now())",
        (owner, listing_id, A_SCORE, json.dumps(A_BREAKDOWN)),
    )
    return World(
        a=a,
        b=b,
        ids={
            "resume": str(resume_id),
            "document": str(document_id),
            "application": application_id,
            "listing": listing_id,
        },
    )


def _fill(path: str, value: str) -> str:
    """``path`` with its first path parameter replaced by ``value``."""
    start, end = path.index("{"), path.index("}")
    return path[:start] + value + path[end + 1 :]


async def _a_still_has_everything(client: AsyncClient, world: World) -> None:
    """B's attempts changed nothing of A's."""
    headers = world.a[1]
    app_resp = await client.get(f"{API_V1}/applications/{world.ids['application']}", headers=headers)
    assert app_resp.status_code == 200
    body = app_resp.json()
    assert body["status"] == "saved" and body["notes"] == A_NOTES
    listing = await client.get(f"{API_V1}/listings/{world.ids['listing']}", headers=headers)
    assert listing.status_code == 200
    resumes = (await client.get(f"{API_V1}/resumes", headers=headers)).text
    assert world.ids["resume"] in resumes
    documents = (await client.get(f"{API_V1}/documents", headers=headers)).text
    assert world.ids["document"] in documents


def _assert_nothing_of_a(text: str, world: World, where: str) -> None:
    """None of A's ids in ``text``. A problem's ``instance`` (the request path B sent, which
    holds A's id) is an echo of B's own request, not a leak, and is left out."""
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict) and "instance" in body:
        body.pop("instance")
        text = json.dumps(body)
    leaked = [name for name, value in world.forbidden().items() if value in text]
    assert not leaked, f"{where} returned A's {', '.join(leaked)} to B"


# --------------------------------------------------------------------------- isolation


@pytest.mark.parametrize(
    ("method", "path"), sorted(REGISTRY), ids=[f"{m} {p}" for m, p in sorted(REGISTRY)]
)
async def test_b_gets_404_for_as_ids(
    client: AsyncClient, world: World, method: str, path: str
) -> None:
    entry = REGISTRY[(method, path)]
    url = _fill(path, world.ids[entry.resource])
    resp = await client.request(method, url, json=entry.json, headers=world.b[1])
    if entry.scoped is not None:
        assert resp.status_code == 200, resp.text
        _assert_nothing_of_a(resp.text, world, f"{method} {path}")
    else:
        assert resp.status_code == 404, f"{method} {path}: {resp.status_code} {resp.text}"
        assert resp.headers["content-type"].startswith(PROBLEM)
        _assert_nothing_of_a(resp.text, world, f"{method} {path}")
    await _a_still_has_everything(client, world)


@pytest.mark.parametrize(
    ("method", "path"), sorted(REGISTRY), ids=[f"{m} {p}" for m, p in sorted(REGISTRY)]
)
async def test_a_reaches_their_own_ids(
    client: AsyncClient, world: World, method: str, path: str
) -> None:
    """The positive control: A's own id is not a 404, so B's 404 means isolation (not a bad
    fixture)."""
    entry = REGISTRY[(method, path)]
    url = _fill(path, world.ids[entry.resource])
    resp = await client.request(method, url, json=entry.json, headers=world.a[1])
    assert resp.status_code < 400, f"{method} {path}: {resp.status_code} {resp.text}"


LIST_ROUTES = sorted(
    r.path for r in ROUTES if r.method == "GET" and not r.has_param and _student_route(r)
)


def _query(path: str, world: World) -> dict[str, str]:
    return {
        k: v.replace("{listing}", world.ids["listing"])
        for k, v in LIST_QUERY.get(path, {}).items()
    }


@pytest.mark.parametrize("path", LIST_ROUTES)
async def test_list_routes_return_nothing_of_as_to_b(
    client: AsyncClient, world: World, path: str
) -> None:
    resp = await client.get(path, params=_query(path, world), headers=world.b[1])
    assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"
    _assert_nothing_of_a(resp.text, world, f"GET {path}")


@pytest.mark.parametrize("path", sorted(LIST_OWN))
async def test_a_sees_their_own_rows_on_the_list_routes(
    client: AsyncClient, world: World, path: str
) -> None:
    resp = await client.get(path, params=_query(path, world), headers=world.a[1])
    assert resp.status_code == 200, resp.text
    assert world.ids[LIST_OWN[path]] in resp.text


def test_list_controls_name_real_list_routes() -> None:
    assert set(LIST_QUERY) <= set(LIST_ROUTES)
    assert set(LIST_OWN) <= set(LIST_ROUTES)


async def test_b_cannot_save_or_attach_as_resources_by_body_id(
    client: AsyncClient, world: World
) -> None:
    """Ids in a request body are checked like ids in a path."""
    headers = world.b[1]
    saved = await client.post(
        f"{API_V1}/applications", json={"listing_id": world.ids["listing"]}, headers=headers
    )
    assert saved.status_code == 404, saved.text
    # B's own Application, pointed at A's Resume version.
    own = await client.post(
        f"{API_V1}/applications/manual", json={"title": "B's own job"}, headers=headers
    )
    assert own.status_code == 201, own.text
    patched = await client.patch(
        f"{API_V1}/applications/{own.json()['id']}",
        json={"resume_id": world.ids["resume"]},
        headers=headers,
    )
    # Not 404: the path's resource (B's own Application) exists, and A's Resume version is a
    # body field. The Story 4.1 contract answers a Resume version that is not the caller's
    # with 422 on ``body.resume_id``, "No such Resume version.", the SAME answer as for an id
    # that exists nowhere, so B learns nothing about A's Resume.
    unknown = await client.patch(
        f"{API_V1}/applications/{own.json()['id']}",
        json={"resume_id": UNKNOWN_ID},
        headers=headers,
    )
    assert patched.status_code == unknown.status_code == 422, patched.text
    assert patched.json()["errors"] == unknown.json()["errors"] == [
        {"loc": ["body", "resume_id"], "msg": "No such Resume version."}
    ]
    _assert_nothing_of_a(patched.text, world, "PATCH /applications/{id} with A's resume_id")
    after = await client.get(f"{API_V1}/applications/{own.json()['id']}", headers=headers)
    assert after.json()["resume_id"] is None


async def test_as_values_are_in_as_own_responses(client: AsyncClient, world: World) -> None:
    """The positive control for :data:`A_TEXTS`: A sees each of them, so B not seeing them
    is meaningful."""
    headers = world.a[1]
    seen = "".join(
        [
            (await client.get(f"{API_V1}/applications/{world.ids['application']}", headers=headers)).text,
            (await client.get(f"{API_V1}/resumes", headers=headers)).text,
            (await client.get(f"{API_V1}/documents", headers=headers)).text,
            (await client.get(f"{API_V1}/matches", headers=headers)).text,
            (await client.get(f"{API_V1}/listings/{world.ids['listing']}/match", headers=headers)).text,
        ]
    )
    missing = [name for name, value in A_TEXTS.items() if value not in seen]
    assert not missing, missing


PROTECTED_ROUTES = sorted(
    (r.method, r.path) for r in ROUTES if r.path.startswith(f"{API_V1}/")
    and not r.path.startswith(f"{API_PUBLIC}/")
)


def test_every_protected_route_is_enumerated() -> None:
    assert len(PROTECTED_ROUTES) > len(REGISTRY) + len(ADMIN_ROUTES)


@pytest.mark.parametrize(
    ("method", "path"), PROTECTED_ROUTES, ids=[f"{m} {p}" for m, p in PROTECTED_ROUTES]
)
async def test_every_non_public_route_is_401_without_a_token(
    client: AsyncClient, tokens: TokenFactory, method: str, path: str
) -> None:
    url = path
    while "{" in url:
        url = _fill(url, UNKNOWN_ID)
    resp = await client.request(method, url)
    assert resp.status_code == 401, f"{method} {path}: {resp.status_code} {resp.text}"
    assert resp.headers["content-type"].startswith(PROBLEM)


# --------------------------------------------------------------------------- admin


@pytest.mark.parametrize(("method", "path"), sorted(ADMIN_ROUTES))
async def test_admin_routes_are_403_to_a_student(
    client: AsyncClient,
    accounts: AccountFactory,
    tokens: TokenFactory,
    method: str,
    path: str,
) -> None:
    _, headers = make_student(accounts, tokens)
    url = path
    while "{" in url:
        url = _fill(url, UNKNOWN_ID)
    resp = await client.request(method, url, headers=headers)
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"
    assert resp.headers["content-type"].startswith(PROBLEM)
