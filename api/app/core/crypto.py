"""Credential encryption (AD-6): AES-256-GCM with versioned master keys and bound AAD.

* ``encrypt(plaintext, aad) -> bytes`` uses the HIGHEST configured key version and a random
  96-bit nonce. ``decrypt(blob, aad) -> str`` uses the version named in the blob, so keys can
  be rotated: add version N+1, re-encrypt at leisure, then drop the old version.
* Blob format: ``b"v" + <version as ASCII digits> + b":" + nonce(12) + ciphertext||tag``.
* ``aad`` binds the ciphertext to where it is stored (``<table>:<row_id>:<owner_id>``), so a
  blob copied to another row or owner fails to decrypt.
* Master keys come from ``CREDENTIALS_MASTER_KEYS`` (Settings). With none configured, both
  functions raise :class:`CredentialsUnavailableError` (503 ``credentials-unavailable``).
* A wrong ``aad``, a tampered blob or an unknown version raises :class:`CredentialError`,
  whose message never includes the plaintext or any key material.
"""

import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import MAX_KEY_VERSION, get_settings
from app.core.errors import CredentialsUnavailableError

NONCE_BYTES = 12
_HEADER_RE = re.compile(rb"^v([1-9][0-9]{0,4}):")


class CredentialError(Exception):
    """A credential blob could not be decrypted (wrong AAD, tampering or unknown key
    version). Never carries plaintext or key material."""


def credential_aad(table: str, row_id: object, owner_id: object) -> str:
    """The AD-6 additional authenticated data: ``<table>:<row_id>:<owner_id>``."""
    return f"{table}:{row_id}:{owner_id}"


def _keys() -> dict[int, bytes]:
    keys = get_settings().master_keys
    if not keys:
        raise CredentialsUnavailableError()
    return keys


def current_key_version() -> int:
    """The version new encryptions use (the highest configured)."""
    return max(_keys())


def encrypt(plaintext: str, aad: str) -> bytes:
    keys = _keys()
    version = max(keys)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(keys[version]).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return b"v" + str(version).encode("ascii") + b":" + nonce + sealed


def _header(blob: bytes) -> tuple[int, int]:
    """``(version, header length)`` of ``blob``. Raises :class:`CredentialError`."""
    match = _HEADER_RE.match(blob)
    if match is None or int(match.group(1)) > MAX_KEY_VERSION:
        raise CredentialError("credential blob has no valid key-version prefix")
    return int(match.group(1)), match.end()


def blob_key_version(blob: bytes) -> int:
    """The key version named in ``blob``'s prefix. Raises :class:`CredentialError`."""
    return _header(bytes(blob))[0]


def decrypt(blob: bytes, aad: str) -> str:
    keys = _keys()
    blob = bytes(blob)
    version, header_len = _header(blob)
    key = keys.get(version)
    if key is None:
        raise CredentialError(f"credential key version {version} is not configured")
    body = blob[header_len:]
    if len(body) < NONCE_BYTES + 16:
        raise CredentialError("credential blob is truncated")
    nonce, sealed = body[:NONCE_BYTES], body[NONCE_BYTES:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, aad.encode("utf-8"))
    except InvalidTag:
        raise CredentialError("credential could not be decrypted") from None
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError("credential could not be decrypted") from None
