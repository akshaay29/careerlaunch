"""Request body size limits for specific routes, enforced BEFORE the body is parsed.

Starlette receives and spools a whole multipart body before a route runs, so a route-level
size check alone would accept a multi-GB upload first. :class:`BodySizeLimitMiddleware`
answers 413 problem+json when ``Content-Length`` is over the limit, and otherwise stops
reading (413) as soon as the received bytes pass it (chunked bodies).
"""

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import problem_response

# (method, path) -> (max body bytes, problem detail)
BodyLimits = dict[tuple[str, str], tuple[int, str]]


class BodyTooLargeError(HTTPException):
    """Raised from ``receive`` once a body passes its limit. An HTTPException, so FastAPI's
    body parsing re-raises it (instead of turning it into a 400) and the problem+json
    handler renders a 413."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=413, detail=detail)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, limits: BodyLimits) -> None:
        self.app = app
        self.limits = {(m.upper(), p.rstrip("/") or "/"): v for (m, p), v in limits.items()}

    async def _reject(self, scope: Scope, receive: Receive, send: Send, detail: str) -> None:
        response = problem_response(
            Request(scope), status=413, slug="payload-too-large", title="Payload too large",
            detail=detail,
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limits.get((scope["method"], scope["path"].rstrip("/") or "/"))
        if limit is None:
            await self.app(scope, receive, send)
            return
        max_bytes, detail = limit

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > max_bytes:
                    await self._reject(scope, receive, send, detail)
                    return
                break

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise BodyTooLargeError(detail)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send, detail)
