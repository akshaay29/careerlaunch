"""Structured JSON logging to stdout with a per-request id. Never log secrets."""

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

REQUEST_ID_HEADER = "x-request-id"
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "color_message"}


REDACTED = "[redacted]"
# Apify API tokens (AD-6): redacted wherever they appear in a log line.
_SECRET_VALUE_RE = re.compile(r"apify_api_[A-Za-z0-9]+")
# Bearer credentials (e.g. an Authorization header echoed in a message or traceback).
_BEARER_RE = re.compile(r"(Bearer)\s+\S+", re.IGNORECASE)
# ``extra`` fields whose lower-cased name CONTAINS one of these are always redacted.
_SECRET_FIELD_PARTS = (
    "token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "master_key",
    "ciphertext",
)


def _is_secret_field(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SECRET_FIELD_PARTS)


def redact(value: Any) -> Any:
    """Mask secret-looking values in ``value`` (strings, and nested dicts / lists / tuples)."""
    if isinstance(value, str):
        return _BEARER_RE.sub(f"Bearer {REDACTED}", _SECRET_VALUE_RE.sub(REDACTED, value))
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _is_secret_field(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value))


class JsonFormatter(logging.Formatter):
    """One JSON object per record. Secrets are redacted: Apify tokens and ``Bearer <x>``
    anywhere (message, fields, traceback) and any ``extra`` field whose name contains
    ``token``, ``authorization``, ``api_key``, ``apikey``, ``secret``, ``password``,
    ``master_key`` or ``ciphertext``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = REDACTED if _is_secret_field(key) else value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True


class RequestIdMiddleware:
    """Pure ASGI middleware: binds a request id (incoming header or new uuid) for logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER.encode())
        rid = incoming.decode("latin-1")[:64] if incoming else uuid.uuid4().hex
        token = request_id_var.set(rid)
        # Also stash on scope state: the 500 handler runs outside this middleware.
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), rid.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
