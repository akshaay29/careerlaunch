"""Typed domain errors and the ONE problem+json handler module (RFC 9457, AD-14).

Every error response has ``type`` (relative URI ``/problems/<slug>``), ``title``,
``status``, ``detail`` and ``instance``. Validation errors add ``errors: [{loc, msg}]``.
"""

import logging
from http import HTTPStatus
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger("app.errors")


def problem_type(slug: str) -> str:
    return f"/problems/{slug}"


class DomainError(Exception):
    """Base for typed domain errors. Subclasses declare ``status``, ``slug`` and ``title``."""

    status: ClassVar[int] = 400
    slug: ClassVar[str] = "domain-error"
    title: ClassVar[str] = "Domain error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class NotFoundError(DomainError):
    status = 404
    slug = "not-found"
    title = "Not found"


class UnauthorizedError(DomainError):
    status = 401
    slug = "unauthorized"
    title = "Unauthorized"


class DrainStaleError(DomainError):
    status = 503
    slug = "drain-stale"
    title = "Drain stale"


class ForbiddenError(DomainError):
    status = 403
    slug = "forbidden"
    title = "Forbidden"


class ConflictError(DomainError):
    status = 409
    slug = "conflict"
    title = "Conflict"


_STATUS_SLUGS: dict[int, str] = {
    400: "bad-request",
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    413: "payload-too-large",
    415: "unsupported-media-type",
    422: "validation-error",
    429: "too-many-requests",
    500: "internal-error",
    503: "service-unavailable",
}


def _slug_for(status: int) -> str:
    if status in _STATUS_SLUGS:
        return _STATUS_SLUGS[status]
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "-")
    except ValueError:
        return "error"


def _title_for(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem_response(
    request: Request,
    *,
    status: int,
    slug: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem_type(slug or _slug_for(status)),
        "title": title or _title_for(status),
        "status": status,
        "detail": detail or title or _title_for(status),
        "instance": request.url.path,
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def _request_id(request: Request) -> str | None:
    return request_id_var.get() or getattr(request.state, "request_id", None)


async def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    return problem_response(
        request,
        status=exc.status_code,
        detail=detail,
        headers=getattr(exc, "headers", None),
    )


async def _validation_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": [str(p) for p in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        slug="validation-error",
        title="Validation error",
        detail="The request is invalid. See errors for details.",
        extra={"errors": errors},
    )


async def _domain_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return problem_response(
        request, status=exc.status, slug=exc.slug, title=exc.title, detail=exc.detail
    )


async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Log as JSON (exception type + traceback) but never echo internals to the client.
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={"path": request.url.path, "method": request.method, "request_id": _request_id(request)},
    )
    return problem_response(
        request,
        status=500,
        slug="internal-error",
        title="Internal Server Error",
        detail="An unexpected error occurred.",
    )


class UnhandledErrorMiddleware:
    """Innermost middleware: turns unhandled exceptions into the 500 problem response.

    Starlette's ServerErrorMiddleware sits OUTSIDE all user middleware, so its responses
    would miss CORS and x-request-id headers. Catching here keeps them.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise
            response = await _unhandled_exception(Request(scope, receive), exc)
            await response(scope, receive, send)


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler. Call BEFORE adding CORS / request-id middleware so the
    unhandled-error middleware ends up innermost."""
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_exception)
    app.add_exception_handler(DomainError, _domain_exception)
    app.add_exception_handler(Exception, _unhandled_exception)
