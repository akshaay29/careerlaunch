"""App factory: CORS, route classes, the problem+json handlers, JSON logging.

Route classes (AD-14) are applied by router-level dependencies, so a new route is protected by
default:

* ``/api/v1/*``         -- valid Supabase token (``include_api_router``)
* ``/api/v1/admin/*``   -- valid token + DB role ``admin`` (``include_admin_router``)
* ``/api/v1/public/*``  -- no token (``include_public_router``)
* ``/internal/*``       -- drain secret, not in the OpenAPI schema (``include_internal_router``)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.adapters import supabase_storage
from app.core.auth import JwksCache, current_actor, install_auth, require_admin
from app.core.config import Settings, get_settings
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.limits import BodySizeLimitMiddleware
from app.core.internal import router as internal_router
from app.core.logging import REQUEST_ID_HEADER, RequestIdMiddleware, configure_logging
from app.modules.accounts.router import admin_router as accounts_admin_router
from app.modules.accounts.router import router as accounts_router
from app.modules.accounts.service import get_or_provision
from app.modules.listings.router import router as listings_router
from app.modules.matching.router import router as matching_router
from app.modules.profile import events as profile_events
from app.modules.profile.files import MAX_FILE_BYTES
from app.modules.profile.router import router as profile_router
from app.modules.profile.service import TOO_LARGE_DETAIL
from app.modules.tracker.router import router as tracker_router
from app.ports.file_store import FileStore, LazyFileStore, install_file_store

API_V1 = "/api/v1"
API_ADMIN = f"{API_V1}/admin"
API_PUBLIC = f"{API_V1}/public"
INTERNAL = "/internal"
# Room for the multipart boundaries and part headers around a 5 MB file.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class Health(BaseModel):
    status: Literal["ok"]


system_router = APIRouter(tags=["system"])


@system_router.get("/health", response_model=Health, operation_id="getHealth")
async def health() -> Health:
    return Health(status="ok")


def _check_no_nested_class(router: APIRouter) -> None:
    """A module router mounted under /api/v1 must not define /admin/* or /public/* paths:
    those would carry the wrong guard."""
    for route in router.routes:
        path = getattr(route, "path", "")
        if path == "/admin" or path.startswith(("/admin/", "/public/")) or path == "/public":
            raise ValueError(f"route {path!r} belongs in the admin or public router class")


def include_api_router(app: FastAPI, router: APIRouter) -> None:
    """Mount under /api/v1. Every route requires a valid token (401 otherwise)."""
    _check_no_nested_class(router)
    app.include_router(router, prefix=API_V1, dependencies=[Depends(current_actor)])


def include_admin_router(app: FastAPI, router: APIRouter) -> None:
    """Mount under /api/v1/admin. Every route requires the admin role (401 / 403 otherwise)."""
    app.include_router(router, prefix=API_ADMIN, dependencies=[Depends(require_admin)])


def include_public_router(app: FastAPI, router: APIRouter) -> None:
    """Mount under /api/v1/public. No token is needed."""
    app.include_router(router, prefix=API_PUBLIC)


def include_internal_router(app: FastAPI, router: APIRouter) -> None:
    """Mount a router under /internal. Internal routes never appear in the OpenAPI schema."""
    app.include_router(router, prefix=INTERNAL, include_in_schema=False)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def install_files(app: FastAPI, store: FileStore | None = None) -> FileStore:
    """Wire the FileStore (``app.state.file_store`` for routers, process-wide for tasks).

    Without an injected store, the Supabase Storage adapter is built from Settings on first
    use, so creating the app (OpenAPI export, tests) never needs the service-role key.
    """
    if store is None:
        store = LazyFileStore(supabase_storage.from_settings)
    app.state.file_store = store
    install_file_store(store)
    return store


def create_app(
    settings: Settings | None = None,
    jwks: JwksCache | None = None,
    file_store: FileStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CareerLaunch API",
        version="0.1.0",
        lifespan=_lifespan,
    )

    install_error_handlers(app)  # first => its middleware is innermost
    # Inside CORS (added below), so a browser can read the 413 detail.
    app.add_middleware(
        BodySizeLimitMiddleware,
        limits={
            ("POST", f"{API_V1}/resumes"): (
                MAX_FILE_BYTES + MULTIPART_OVERHEAD_BYTES,
                TOO_LARGE_DETAIL,
            )
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens only; the api uses no cookies (AD-14)
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestIdMiddleware)

    # The role is loaded by accounts.service, injected here so core never imports modules.
    install_auth(app, account_loader=get_or_provision, jwks=jwks)
    install_files(app, file_store)
    # Domain-event subscribers, registered explicitly at startup (idempotent).
    profile_events.register()

    # Admin and public first: routes match in mount order, so a token-only route such as
    # /{id}/... can never shadow /api/v1/admin/* or /api/v1/public/*.
    include_public_router(app, system_router)
    include_admin_router(app, accounts_admin_router)
    for router in (accounts_router, profile_router, listings_router, matching_router, tracker_router):
        include_api_router(app, router)
    include_internal_router(app, internal_router)
    return app


app = create_app()
