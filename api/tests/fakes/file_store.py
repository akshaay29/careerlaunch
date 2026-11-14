"""In-memory FileStore fake (AD-3). Tests never touch real Storage."""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

from app.ports.file_store import FileStoreError


@dataclass
class StoredObject:
    data: bytes
    content_type: str


@dataclass
class SignedUrl:
    path: str
    expires_in_seconds: int
    download_name: str
    url: str


@dataclass
class FakeFileStore:
    """Objects by path. Set ``fail_put`` / ``fail_delete`` / ``fail_sign`` / ``fail_get`` to make an
    operation raise :class:`FileStoreError`."""

    objects: dict[str, StoredObject] = field(default_factory=dict)
    signed: list[SignedUrl] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    puts: int = 0
    fail_put: bool = False
    # Write the object, THEN raise (e.g. a read timeout after Storage stored it).
    fail_put_after_write: bool = False
    fail_delete: bool = False
    fail_sign: bool = False
    fail_get: bool = False
    gets: int = 0

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise FileStoreError("fake put failure")
        self.puts += 1
        self.objects[path] = StoredObject(bytes(data), content_type)
        if self.fail_put_after_write:
            raise FileStoreError("fake put failure after write")

    async def get(self, path: str) -> bytes:
        if self.fail_get:
            raise FileStoreError("fake get failure")
        self.gets += 1
        stored = self.objects.get(path)
        if stored is None:
            raise FileStoreError("fake get failure: no such object")
        return stored.data

    async def delete(self, paths: Sequence[str]) -> None:
        if self.fail_delete:
            raise FileStoreError("fake delete failure")
        for path in paths:
            self.deleted.append(path)
            self.objects.pop(path, None)

    async def signed_download_url(
        self, path: str, expires_in_seconds: int, download_name: str
    ) -> str:
        if self.fail_sign:
            raise FileStoreError("fake sign failure")
        if path not in self.objects:
            raise FileStoreError("fake sign failure: no such object")
        expires = int(time.time()) + expires_in_seconds
        url = (
            f"https://storage.fake/object/sign/private/{path}?token=t{len(self.signed)}"
            f"&expires={expires}&download={quote(download_name, safe='')}"
        )
        self.signed.append(SignedUrl(path, expires_in_seconds, download_name, url))
        return url

    def paths_under(self, prefix: str) -> list[str]:
        return sorted(p for p in self.objects if p.startswith(prefix))
