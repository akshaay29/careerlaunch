"""AD-6: core.crypto (AES-256-GCM, versioned master keys, bound AAD) and its settings."""

import base64
import os
from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from app.core import crypto
from app.core.config import Settings, get_settings, parse_master_keys
from app.core.crypto import CredentialError, credential_aad, decrypt, encrypt
from app.core.errors import CredentialsUnavailableError


def make_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


K1, K2 = make_key(), make_key()
AAD = credential_aad("provider_credentials", "row-1", "owner-1")
TOKEN = "apify_api_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"


@pytest.fixture
def master_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    def set_keys(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("CREDENTIALS_MASTER_KEYS", raising=False)
        else:
            monkeypatch.setenv("CREDENTIALS_MASTER_KEYS", value)
        get_settings.cache_clear()

    set_keys(f"1:{K1}")
    yield set_keys  # type: ignore[misc]
    monkeypatch.undo()
    get_settings.cache_clear()


def test_round_trip(master_keys) -> None:
    blob = encrypt(TOKEN, AAD)
    assert blob.startswith(b"v1:")
    assert TOKEN.encode() not in blob
    assert decrypt(blob, AAD) == TOKEN


def test_nonce_is_random(master_keys) -> None:
    assert encrypt(TOKEN, AAD) != encrypt(TOKEN, AAD)


def test_wrong_aad_fails(master_keys) -> None:
    blob = encrypt(TOKEN, AAD)
    other = credential_aad("provider_credentials", "row-2", "owner-1")
    with pytest.raises(CredentialError) as exc:
        decrypt(blob, other)
    assert TOKEN not in str(exc.value) and K1 not in str(exc.value)


@pytest.mark.parametrize("position", [3, 10, -1])
def test_tampered_blob_fails(master_keys, position: int) -> None:
    blob = bytearray(encrypt(TOKEN, AAD))
    blob[position] ^= 0x01
    with pytest.raises(CredentialError):
        decrypt(bytes(blob), AAD)


@pytest.mark.parametrize("blob", [b"", b"x1:abc", b"v:abc", b"v1:short", b"v01:" + b"0" * 40])
def test_malformed_blob_fails(master_keys, blob: bytes) -> None:
    with pytest.raises(CredentialError):
        decrypt(blob, AAD)


def test_unknown_version_fails(master_keys) -> None:
    master_keys(f"2:{K2}")
    blob = encrypt(TOKEN, AAD)
    master_keys(f"1:{K1}")
    with pytest.raises(CredentialError, match="version 2"):
        decrypt(blob, AAD)


def test_rotation(master_keys) -> None:
    old = encrypt(TOKEN, AAD)
    master_keys(f"1:{K1},2:{K2}")
    assert decrypt(old, AAD) == TOKEN
    new = encrypt(TOKEN, AAD)
    assert new.startswith(b"v2:")
    assert crypto.blob_key_version(new) == 2 and crypto.blob_key_version(old) == 1
    assert decrypt(new, AAD) == TOKEN


def test_no_master_key_is_unavailable(master_keys) -> None:
    master_keys(None)
    with pytest.raises(CredentialsUnavailableError):
        encrypt(TOKEN, AAD)
    with pytest.raises(CredentialsUnavailableError):
        decrypt(b"v1:" + b"0" * 40, AAD)


@pytest.mark.parametrize(
    "value",
    [
        "nonsense",
        "1:not-base64!",
        f"0:{K1}",
        f"1:{K1},1:{K2}",
        "1:" + base64.b64encode(b"short").decode(),
        f"x:{K1}",
        f"\u0661:{K1}",  # ARABIC-INDIC DIGIT ONE: isdigit() but not an ASCII decimal
        f"\u00b2:{K1}",  # SUPERSCRIPT TWO: isdigit() but not isdecimal()
        f"32768:{K1}",
        ",",
    ],
)
def test_settings_reject_bad_master_keys(value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(credentials_master_keys=value)  # type: ignore[arg-type]
    assert K1 not in str(exc.value)


def test_settings_accept_and_parse_keys() -> None:
    settings = Settings(credentials_master_keys=f" 1:{K1} , 2:{K2} ")  # type: ignore[arg-type]
    assert set(settings.master_keys) == {1, 2}
    assert parse_master_keys(f"1:{K1}")[1] == base64.b64decode(K1)
    assert Settings(credentials_master_keys="").master_keys == {}  # type: ignore[arg-type]


def test_header_version_above_smallint_fails(master_keys) -> None:
    blob = b"v40000:" + b"0" * 40
    with pytest.raises(CredentialError):
        decrypt(blob, AAD)
    with pytest.raises(CredentialError):
        crypto.blob_key_version(blob)


@pytest.mark.parametrize(
    ("environment", "base", "ok"),
    [
        ("prod", "https://api.apify.com", True),
        ("prod", "http://api.apify.com", False),
        ("prod", "ftp://api.apify.com", False),
        ("prod", "not a url", False),
        ("local", "http://127.0.0.1:9999", True),
        ("ci", "http://apify.test", True),
        ("local", "apify.test", False),
    ],
)
def test_apify_api_base_must_be_https_outside_local_and_ci(
    environment: str, base: str, ok: bool
) -> None:
    kwargs = {"environment": environment, "apify_api_base": base}
    if environment == "prod":
        kwargs["supabase_url"] = "https://project.supabase.co"
    if ok:
        Settings(**kwargs)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValidationError, match="APIFY_API_BASE"):
            Settings(**kwargs)  # type: ignore[arg-type]
