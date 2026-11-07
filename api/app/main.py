"""App factory: CORS, routers under /api/v1, the problem+json handlers, JSON logging."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import REQUEST_ID_HEADER, RequestIdMiddleware, configure_logging
from app.modules.accounts.router import router as accounts_router
from app.modules.listings.router import router as listings_router
from app.modules.matching.router import router as matching_router
from app.modules.profile.router import router as profile_router
from app.modules.tracker.router import router as tracker_router

API_V1 = "/api/v1"
INTERNAL = "/internal"


class Health(BaseModel):
    status: Literal["ok"]


system_router = APIRouter(tags=["system"])


@system_router.get("/health", response_model=Health, operation_id="getHealth")
async def health() -> Health:
    return Health(status="ok")


def include_internal_router(app: FastAPI, router: APIRouter) -> None:
    """Mount a router under /internal. Internal routes never appear in the OpenAPI schema."""
    app.include_router(router, prefix=INTERNAL, include_in_schema=False)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CareerLaunch API",
        version="0.1.0",
        lifespan=_lifespan,
    )

    install_error_handlers(app)  # first => its middleware is innermost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens only; the api uses no cookies (AD-14)
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(system_router, prefix=API_V1)
    app.include_router(accounts_router, prefix=API_V1)
    app.include_router(profile_router, prefix=API_V1)
    app.include_router(listings_router, prefix=API_V1)
    app.include_router(matching_router, prefix=API_V1)
    app.include_router(tracker_router, prefix=API_V1)
    return app


app = create_app()
