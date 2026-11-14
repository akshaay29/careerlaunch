"""profile.service: Resume versions (FR-3, Files convention).

Takes the session and the FileStore; never commits (routers do).

* Upload: size (5 MiB) and content-type sniffing, then the version cap, then the file is
  stored at ``<owner_id>/resume/<id>`` and THEN the row is inserted. If the insert (or the
  caller's commit, via :func:`discard_upload`) fails, the stored file is deleted (best
  effort) and the error propagates. A Student without an active Resume gets the new upload
  as the active one, which publishes ``ResumeActivated``.
* Activation deactivates the current row, activates the new one and publishes
  ``ResumeActivated`` in the caller's unit of work.
* Delete removes the row and enqueues ``profile.delete_stored_files`` in the same
  transaction, so the object is removed even if Storage is down right now.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.core.events import ResumeActivated, publish
from app.core.jobs import enqueue
from app.modules.profile import repo
from app.modules.profile.files import (
    MAX_FILE_BYTES,
    sanitize_filename,
    sniff_resume_type,
    storage_path,
)
from app.modules.profile.models import Resume
from app.modules.profile.tasks import DELETE_STORED_FILES
from app.ports.file_store import FileStore

logger = logging.getLogger("app.profile")

TOO_LARGE_DETAIL = "Resume must be 5 MB or smaller"
WRONG_TYPE_DETAIL = "Upload a PDF or DOCX file"
CAP_DETAIL = "Delete an old version first"
NOT_FOUND_DETAIL = "No such Resume version."
# Signed download URLs live at most 5 minutes (Files convention).
DOWNLOAD_URL_TTL_SECONDS = 300


def resume_path(owner_id: uuid.UUID, resume_id: uuid.UUID) -> str:
    return storage_path(owner_id, "resume", resume_id)


def check_upload(data: bytes) -> str:
    """Validate an upload's bytes; returns its content type. Raises 413 / 415 errors."""
    if len(data) > MAX_FILE_BYTES:
        raise PayloadTooLargeError(TOO_LARGE_DETAIL)
    content_type = sniff_resume_type(data) if data else None
    if content_type is None:
        raise UnsupportedMediaTypeError(WRONG_TYPE_DETAIL)
    return content_type


async def _delete_quietly(store: FileStore, path: str) -> None:
    try:
        await store.delete([path])
    except Exception:
        # Best effort: the object is unreachable without a row; log the id, not the name.
        logger.exception("resume_upload_cleanup_failed", extra={"path": path})


async def _discard(store: FileStore, path: str) -> None:
    """Delete an orphaned upload. Shielded, so a cancelled request still removes it."""
    try:
        await asyncio.shield(_delete_quietly(store, path))
    except asyncio.CancelledError:
        # The caller was cancelled again while waiting; the shielded delete keeps running.
        pass


async def upload_resume(
    session: AsyncSession,
    store: FileStore,
    owner_id: uuid.UUID,
    *,
    filename: str | None,
    data: bytes,
    max_versions: int,
) -> Resume:
    """Store a new Resume version and insert its row (not committed).

    Raises :class:`PayloadTooLargeError`, :class:`UnsupportedMediaTypeError` or
    :class:`ConflictError` (version cap) before anything is stored.
    """
    content_type = check_upload(data)
    await repo.lock_owner_resumes(session, owner_id)
    if await repo.count(session, owner_id) >= max_versions:
        raise ConflictError(CAP_DETAIL, extra={"max_versions": max_versions})
    becomes_active = not await repo.has_active(session, owner_id)

    resume_id = uuid.uuid7()
    path = resume_path(owner_id, resume_id)
    try:
        # Inside the try: put may fail AFTER the object was written (e.g. a read timeout).
        await store.put(path, data, content_type)
        resume = await repo.insert_resume(
            session,
            resume_id=resume_id,
            owner_id=owner_id,
            filename=sanitize_filename(filename, content_type),
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            is_active=becomes_active,
        )
        if becomes_active:
            await publish(session, ResumeActivated(owner_id=owner_id, resume_id=resume_id))
    except BaseException:  # cancellation too: never orphan a private file
        await _discard(store, path)
        raise
    return resume


async def discard_upload(store: FileStore, resume: Resume) -> None:
    """The caller's commit failed after :func:`upload_resume`: delete the stored file."""
    await _discard(store, resume_path(resume.owner_id, resume.id))


@dataclass(frozen=True)
class ResumePage:
    items: list[Resume]
    next_cursor: uuid.UUID | None


async def list_resumes(
    session: AsyncSession, owner_id: uuid.UUID, *, limit: int, cursor: uuid.UUID | None
) -> ResumePage:
    """The owner's versions, newest first. ``next_cursor`` is set when more may follow."""
    rows = await repo.list_page(session, owner_id, limit=limit + 1, before=cursor)
    items = rows[:limit]
    next_cursor = items[-1].id if len(rows) > limit else None
    return ResumePage(items=items, next_cursor=next_cursor)


async def _owned(session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
    resume = await repo.get(session, owner_id, resume_id)
    if resume is None:
        # Another Student's id and an unknown id look the same (AD-5).
        raise NotFoundError(NOT_FOUND_DETAIL)
    return resume


async def activate_resume(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> tuple[Resume, bool]:
    """Make ``resume_id`` the owner's only active version. Returns ``(resume, changed)``;
    an already active version changes nothing and publishes nothing."""
    await repo.lock_owner_resumes(session, owner_id)
    resume = await _owned(session, owner_id, resume_id)
    if resume.is_active:
        return resume, False
    await repo.set_active(session, owner_id, resume_id)
    await publish(session, ResumeActivated(owner_id=owner_id, resume_id=resume_id))
    resume = await _owned(session, owner_id, resume_id)
    return resume, True


@dataclass(frozen=True)
class DownloadLink:
    url: str
    expires_at: datetime


async def download_link(
    session: AsyncSession, store: FileStore, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> DownloadLink:
    """A short-lived signed URL that downloads the version as an attachment."""
    resume = await _owned(session, owner_id, resume_id)
    # Computed before signing, so the stated expiry is never later than the real one.
    expires_at = datetime.now(UTC) + timedelta(seconds=DOWNLOAD_URL_TTL_SECONDS)
    url = await store.signed_download_url(
        resume_path(owner_id, resume.id), DOWNLOAD_URL_TTL_SECONDS, resume.filename
    )
    return DownloadLink(url=url, expires_at=expires_at)


async def delete_resume(session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    """Delete the row and enqueue the object's deletion in the same transaction. Deleting
    the active version leaves no version active."""
    await repo.lock_owner_resumes(session, owner_id)
    deleted = await repo.delete_resume(session, owner_id, resume_id)
    if deleted is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    await enqueue(
        session,
        DELETE_STORED_FILES,
        f"{DELETE_STORED_FILES}:resume:{resume_id}",
        owner_id=owner_id,
        resume_id=resume_id,
    )
