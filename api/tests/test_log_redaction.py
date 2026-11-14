"""AD-6 / logging convention: the JSON formatter redacts Apify tokens and secret fields."""

import json
import logging

import pytest

from app.core.logging import REDACTED, JsonFormatter

TOKEN = "apify_api_Zx9yW8vU7tS6rQ5pO4nM3lK2jI1h"


def _format(msg: str, *args: object, extra: dict | None = None, exc: bool = False) -> str:
    record = logging.getLogger("test.redaction").makeRecord(
        "test.redaction", logging.INFO, __file__, 1, msg, args, None, extra=extra
    )
    if exc:
        try:
            raise ValueError(f"boom with {TOKEN}")
        except ValueError:
            import sys

            record.exc_info = sys.exc_info()
    return JsonFormatter().format(record)


def test_token_in_message_is_redacted() -> None:
    out = _format("saving %s for user", TOKEN)
    assert TOKEN not in out
    assert json.loads(out)["msg"] == f"saving {REDACTED} for user"


@pytest.mark.parametrize("field", ["token", "Authorization", "API_KEY", "secret"])
def test_secret_fields_are_redacted(field: str) -> None:
    out = _format("event", extra={field: "plain-value-123", "status": 401})
    payload = json.loads(out)
    assert payload[field] == REDACTED
    assert payload["status"] == 401
    assert "plain-value-123" not in out


def test_token_in_other_fields_and_nested_values_is_redacted() -> None:
    out = _format(
        "event",
        extra={"token": TOKEN, "detail": f"header was Bearer {TOKEN}", "nested": {"api_key": "k", "ok": TOKEN}},
    )
    assert TOKEN not in out
    payload = json.loads(out)
    assert payload["token"] == REDACTED
    assert payload["detail"] == f"header was Bearer {REDACTED}"
    assert payload["nested"] == {"api_key": REDACTED, "ok": REDACTED}


def test_token_in_exception_text_is_redacted() -> None:
    out = _format("failed", exc=True)
    assert TOKEN not in out
    assert REDACTED in json.loads(out)["exc"]


def test_ordinary_records_are_unchanged() -> None:
    payload = json.loads(_format("hello %s", "world", extra={"count": 3, "status": "ok"}))
    assert payload["msg"] == "hello world"
    assert payload["count"] == 3 and payload["status"] == "ok"


@pytest.mark.parametrize(
    "field",
    [
        "access_token",
        "refresh_token",
        "X-Authorization",
        "apikey",
        "client_secret",
        "db_password",
        "credentials_master_key",
        "ciphertext",
        "token_count",
    ],
)
def test_fields_containing_a_secret_word_are_redacted(field: str) -> None:
    payload = json.loads(_format("event", extra={field: "plain-value-123"}))
    assert payload[field] == REDACTED


def test_bearer_values_are_redacted_in_messages_and_tracebacks() -> None:
    out = _format("sent Authorization: Bearer abc.def-123 to %s", "host")
    assert "abc.def-123" not in out
    assert json.loads(out)["msg"] == f"sent Authorization: Bearer {REDACTED} to host"

    record = logging.getLogger("test.redaction").makeRecord(
        "test.redaction", logging.ERROR, __file__, 1, "failed", (), None
    )
    try:
        raise RuntimeError("header bearer xyz987secret rejected")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    out = JsonFormatter().format(record)
    assert "xyz987secret" not in out
