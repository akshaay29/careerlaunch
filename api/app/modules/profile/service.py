"""profile.service: Resume versions (FR-3) and Documents (FR-26), Files convention.

Takes the session and the FileStore; never commits (routers do).

Private files (shared by Resumes and Documents):

* Upload: size (5 MiB) and content-type sniffing, then the cap, then the file is stored at
  ``<owner_id>/<kind>/<id>`` and THEN the row is inserted. If the insert (or the caller's
  commit, via :func:`discard_upload` / :func:`discard_document_upload`) fails or the request
  is cancelled, the stored file is deleted (best effort, shielded) and the error propagates.
* Download is a signed URL (5 minutes or less) that downloads the file as an attachment
  under its stored filename.
* Delete removes the row and enqueues the object's deletion job in the same transaction, so
  the object is removed even if Storage is down right now.

Resumes: a Student without an active Resume gets the new upload as the active one, which
publishes ``ResumeActivated``. Activation deactivates the current row, activates the new one
and publishes ``ResumeActivated`` in the caller's unit of work.

Documents: PDF, DOCX or plain text; at most ``DOCUMENT_MAX_COUNT``. They publish no events
(Documents do not affect matching).

Skill Profile (FR-4, AD-8):

* ``ResumeActivated`` marks the profile ``pending`` for the new Resume and enqueues
  ``profile.extract_resume`` (``priority``) in the publisher's transaction.
* The job stores the extracted set (with ``vocab_version``) and publishes
  ``SkillProfileChanged``. The Student's ``added`` / ``removed`` / ``custom`` edits are kept.
* Effective skills are ``(extracted | added) - removed``; custom skills are never scored.

Preferences (FR-5):

* One row per owner; no row means :data:`PREFERENCE_DEFAULTS`.
* A save replaces the whole object. Only when the effective values differ from the stored
  ones (or nothing was stored yet) is the row written and ``PreferencesChanged`` published.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import skills
from app.core.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.core.events import PreferencesChanged, ResumeActivated, SkillProfileChanged, publish
from app.core.jobs import enqueue
from app.modules.profile import repo
from app.modules.profile.files import (
    MAX_FILE_BYTES,
    FileKind,
    document_filename,
    sanitize_filename,
    sniff_document_type,
    sniff_resume_type,
    storage_path,
)
from app.modules.profile.extraction import ExtractionResult
from app.modules.profile.models import Document, Preferences, Resume, SkillProfile
from app.modules.profile.schemas import MAX_CUSTOM_SKILLS
from app.modules.profile.tasks import DELETE_DOCUMENT_FILE, DELETE_STORED_FILES, EXTRACT_RESUME
from app.ports.file_store import FileStore

logger = logging.getLogger("app.profile")

TOO_LARGE_DETAIL = "Resume must be 5 MB or smaller"
WRONG_TYPE_DETAIL = "Upload a PDF or DOCX file"
CAP_DETAIL = "Delete an old version first"
NOT_FOUND_DETAIL = "No such Resume version."

DOCUMENT_TOO_LARGE_DETAIL = "Document must be 5 MB or smaller"
DOCUMENT_WRONG_TYPE_DETAIL = "Upload a PDF, DOCX or plain-text file"
DOCUMENT_CAP_DETAIL = "Delete an old document first"
DOCUMENT_NOT_FOUND_DETAIL = "No such Document."

# Signed download URLs live at most 5 minutes (Files convention).
DOWNLOAD_URL_TTL_SECONDS = 300


# --------------------------------------------------------------------------- private files


def _check_file(
    data: bytes, sniff: Callable[[bytes], str | None], *, too_large: str, wrong_type: str
) -> str:
    """Validate an upload's bytes; returns its content type. Raises 413 / 415 errors."""
    if len(data) > MAX_FILE_BYTES:
        raise PayloadTooLargeError(too_large)
    content_type = sniff(data) if data else None
    if content_type is None:
        raise UnsupportedMediaTypeError(wrong_type)
    return content_type


async def _delete_quietly(store: FileStore, path: str) -> None:
    try:
        await store.delete([path])
    except Exception:
        # Best effort: the object is unreachable without a row; log the id, not the name.
        logger.exception("upload_cleanup_failed", extra={"path": path})


async def _discard(store: FileStore, path: str) -> None:
    """Delete an orphaned upload. Shielded, so a cancelled request still removes it."""
    try:
        await asyncio.shield(_delete_quietly(store, path))
    except asyncio.CancelledError:
        # The caller was cancelled again while waiting; the shielded delete keeps running.
        pass


async def _store_then_insert[T](
    store: FileStore,
    path: str,
    data: bytes,
    content_type: str,
    insert: Callable[[], Awaitable[T]],
) -> T:
    """Store the object, then run ``insert`` (the row and anything else in the caller's unit
    of work). On any failure, cancellation included, the stored object is discarded."""
    try:
        # Inside the try: put may fail AFTER the object was written (e.g. a read timeout).
        await store.put(path, data, content_type)
        return await insert()
    except BaseException:  # cancellation too: never orphan a private file
        await _discard(store, path)
        raise


@dataclass(frozen=True)
class DownloadLink:
    url: str
    expires_at: datetime


async def _signed_link(store: FileStore, path: str, filename: str) -> DownloadLink:
    """A short-lived signed URL that downloads ``path`` as an attachment named ``filename``."""
    # Computed before signing, so the stated expiry is never later than the real one.
    expires_at = datetime.now(UTC) + timedelta(seconds=DOWNLOAD_URL_TTL_SECONDS)
    url = await store.signed_download_url(path, DOWNLOAD_URL_TTL_SECONDS, filename)
    return DownloadLink(url=url, expires_at=expires_at)


async def _enqueue_file_deletion(
    session: AsyncSession, task_name: str, kind: FileKind, file_id: uuid.UUID, **ids: uuid.UUID
) -> None:
    """Enqueue the deletion job of a deleted row's object, in the caller's transaction."""
    await enqueue(session, task_name, f"{task_name}:{kind}:{file_id}", **ids)


def _split_page[T: (Resume, Document)](
    rows: list[T], limit: int
) -> tuple[list[T], uuid.UUID | None]:
    """``rows`` was fetched with ``limit + 1``: the page and its ``next_cursor``."""
    items = rows[:limit]
    return items, (items[-1].id if len(rows) > limit else None)


# --------------------------------------------------------------------------- Resumes


def resume_path(owner_id: uuid.UUID, resume_id: uuid.UUID) -> str:
    return storage_path(owner_id, "resume", resume_id)


def check_upload(data: bytes) -> str:
    """Validate a Resume upload's bytes; returns its content type. Raises 413 / 415 errors."""
    return _check_file(
        data, sniff_resume_type, too_large=TOO_LARGE_DETAIL, wrong_type=WRONG_TYPE_DETAIL
    )


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

    async def insert() -> Resume:
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
        return resume

    return await _store_then_insert(
        store, resume_path(owner_id, resume_id), data, content_type, insert
    )


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
    items, next_cursor = _split_page(rows, limit)
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


async def download_link(
    session: AsyncSession, store: FileStore, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> DownloadLink:
    """A short-lived signed URL that downloads the version as an attachment."""
    resume = await _owned(session, owner_id, resume_id)
    return await _signed_link(store, resume_path(owner_id, resume.id), resume.filename)


async def delete_resume(session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    """Delete the row and enqueue the object's deletion in the same transaction. Deleting
    the active version leaves no version active."""
    await repo.lock_owner_resumes(session, owner_id)
    # Before the delete (the FK then sets resume_id to NULL): a pending extraction of this
    # Resume will never land, so the profile must not stay pending forever.
    await repo.settle_pending_skill_profile(session, owner_id, resume_id)
    deleted = await repo.delete_resume(session, owner_id, resume_id)
    if deleted is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    await _enqueue_file_deletion(
        session, DELETE_STORED_FILES, "resume", resume_id, owner_id=owner_id, resume_id=resume_id
    )


# --------------------------------------------------------------------------- Documents


def document_path(owner_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return storage_path(owner_id, "document", document_id)


def check_document_upload(data: bytes) -> str:
    """Validate a Document upload's bytes; returns its content type (PDF, DOCX or
    ``text/plain``). Raises 413 / 415 errors."""
    return _check_file(
        data,
        sniff_document_type,
        too_large=DOCUMENT_TOO_LARGE_DETAIL,
        wrong_type=DOCUMENT_WRONG_TYPE_DETAIL,
    )


async def upload_document(
    session: AsyncSession,
    store: FileStore,
    owner_id: uuid.UUID,
    *,
    filename: str | None,
    data: bytes,
    max_count: int,
) -> Document:
    """Store a new Document and insert its row (not committed).

    Raises :class:`PayloadTooLargeError`, :class:`UnsupportedMediaTypeError` or
    :class:`ConflictError` (the cap) before anything is stored.
    """
    content_type = check_document_upload(data)
    await repo.lock_owner_documents(session, owner_id)
    if await repo.count_documents(session, owner_id) >= max_count:
        raise ConflictError(DOCUMENT_CAP_DETAIL, extra={"max_count": max_count})

    document_id = uuid.uuid7()

    async def insert() -> Document:
        return await repo.insert_document(
            session,
            document_id=document_id,
            owner_id=owner_id,
            filename=document_filename(filename, content_type),
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    return await _store_then_insert(
        store, document_path(owner_id, document_id), data, content_type, insert
    )


async def discard_document_upload(store: FileStore, document: Document) -> None:
    """The caller's commit failed after :func:`upload_document`: delete the stored file."""
    await _discard(store, document_path(document.owner_id, document.id))


@dataclass(frozen=True)
class DocumentPage:
    items: list[Document]
    next_cursor: uuid.UUID | None


async def list_documents(
    session: AsyncSession, owner_id: uuid.UUID, *, limit: int, cursor: uuid.UUID | None
) -> DocumentPage:
    """The owner's Documents, newest first. ``next_cursor`` is set when more may follow."""
    rows = await repo.list_documents_page(session, owner_id, limit=limit + 1, before=cursor)
    items, next_cursor = _split_page(rows, limit)
    return DocumentPage(items=items, next_cursor=next_cursor)


async def document_download_link(
    session: AsyncSession, store: FileStore, owner_id: uuid.UUID, document_id: uuid.UUID
) -> DownloadLink:
    """A short-lived signed URL that downloads the Document as an attachment."""
    document = await repo.get_document(session, owner_id, document_id)
    if document is None:
        # Another Student's id and an unknown id look the same (AD-5).
        raise NotFoundError(DOCUMENT_NOT_FOUND_DETAIL)
    return await _signed_link(store, document_path(owner_id, document.id), document.filename)


async def delete_document(
    session: AsyncSession, owner_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """Delete the row and enqueue the object's deletion in the same transaction."""
    await repo.lock_owner_documents(session, owner_id)
    deleted = await repo.delete_document(session, owner_id, document_id)
    if deleted is None:
        raise NotFoundError(DOCUMENT_NOT_FOUND_DETAIL)
    await _enqueue_file_deletion(
        session,
        DELETE_DOCUMENT_FILE,
        "document",
        document_id,
        owner_id=owner_id,
        document_id=document_id,
    )


# --------------------------------------------------------------------------- Skill Profile

UNREADABLE_MESSAGE = (
    "We couldn't read text from this Resume. Upload a text-based PDF or add your skills manually."
)


async def on_resume_activated(session: AsyncSession, event: ResumeActivated) -> None:
    """``ResumeActivated`` subscriber (registered by ``profile.events.register`` at app
    wiring): mark the profile pending and enqueue the extraction, in the publisher's
    transaction."""
    await repo.mark_skill_profile_pending(session, event.owner_id, event.resume_id)
    await enqueue(
        session,
        EXTRACT_RESUME,
        f"{EXTRACT_RESUME}:resume:{event.resume_id}",
        owner_id=event.owner_id,
        resume_id=event.resume_id,
    )


async def extraction_target(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID
) -> Resume | None:
    """The Resume to extract, or None when it is gone or no longer active (stale job)."""
    resume = await repo.get(session, owner_id, resume_id)
    if resume is None or not resume.is_active:
        return None
    return resume


async def save_extraction(
    session: AsyncSession, owner_id: uuid.UUID, resume_id: uuid.UUID, result: ExtractionResult
) -> bool:
    """Store an extraction result and publish ``SkillProfileChanged``. A no-op (returns
    False) when the Resume was deleted or another one was activated meanwhile."""
    # The same lock as activation and deletion, so the Resume cannot change under us.
    await repo.lock_owner_resumes(session, owner_id)
    # Edits compute `removed` against the extracted set: serialise with them.
    await repo.lock_owner_skill_profile(session, owner_id)
    if await extraction_target(session, owner_id, resume_id) is None:
        return False
    stored = await repo.store_extraction(
        session,
        owner_id,
        resume_id,
        status=result.status,
        extracted_skills=skills.sorted_ids(result.skills),
        vocab_version=skills.VOCAB_VERSION,
        education=list(result.education),
        experience=list(result.experience),
        extracted_at=datetime.now(UTC),
    )
    if stored:
        await publish(session, SkillProfileChanged(owner_id=owner_id))
    return stored


async def get_skill_profile(session: AsyncSession, owner_id: uuid.UUID) -> SkillProfile | None:
    return await repo.get_skill_profile(session, owner_id)


def effective_skills(profile: SkillProfile | None) -> set[str]:
    """``(extracted | added) - removed``: the skills used for matching. Custom skills are
    never included."""
    if profile is None:
        return set()
    return (set(profile.extracted_skills) | set(profile.added_skills)) - set(
        profile.removed_skills
    )


def has_edits(profile: SkillProfile | None) -> bool:
    """True when the Student added, removed or entered custom skills."""
    return profile is not None and bool(
        profile.added_skills or profile.removed_skills or profile.custom_skills
    )


def clean_term(term: str) -> str:
    """A skill term as stored: surrounding whitespace trimmed, inner runs collapsed."""
    return " ".join(term.split())


async def edit_skill_profile(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    add: list[str],
    remove: list[str],
    remove_custom: list[str],
) -> tuple[SkillProfile | None, bool]:
    """Apply a Student's edits over the extracted set; returns ``(profile, changed)``.

    * ``add``: a vocabulary term goes to ``added`` (and leaves ``removed``); any other term
      goes to ``custom`` (case-insensitive de-duplication, at most 50 in total).
    * ``remove``: skill ids. An added skill leaves ``added``; an extracted one goes to
      ``removed``. An id outside the vocabulary is a 422.
    * ``remove_custom``: custom terms to drop (case-insensitive).

    Works without a row (manual entry without a Resume): one is created with no status.
    Publishes ``SkillProfileChanged`` only when something changed.
    """
    for i, skill_id in enumerate(remove):
        if not skills.is_skill_id(skill_id):
            raise InvalidInputError(["body", "remove", str(i)], "Unknown skill id.")

    await repo.lock_owner_skill_profile(session, owner_id)
    profile = await repo.get_skill_profile(session, owner_id)
    extracted = set(profile.extracted_skills) if profile else set()
    before_added = list(profile.added_skills) if profile else []
    before_removed = list(profile.removed_skills) if profile else []
    before_custom = list(profile.custom_skills) if profile else []
    added, removed, custom = list(before_added), list(before_removed), list(before_custom)

    for raw in add:
        term = clean_term(raw)
        skill_id = skills.lookup(term)
        if skill_id is not None:
            if skill_id not in added:
                added.append(skill_id)
            removed = [r for r in removed if r != skill_id]
        elif term.casefold() not in {c.casefold() for c in custom}:
            custom.append(term)

    for skill_id in remove:
        added = [a for a in added if a != skill_id]
        if skill_id in extracted and skill_id not in removed:
            removed.append(skill_id)

    dropped = {clean_term(t).casefold() for t in remove_custom}
    custom = [c for c in custom if c.casefold() not in dropped]
    if len(custom) > MAX_CUSTOM_SKILLS:
        raise InvalidInputError(
            ["body", "add"], f"You can keep at most {MAX_CUSTOM_SKILLS} custom skills."
        )

    if set(added) == set(before_added) and set(removed) == set(before_removed) and (
        custom == before_custom
    ):
        return profile, False

    await repo.save_skill_edits(
        session,
        owner_id,
        added=skills.sorted_ids(added),
        removed=skills.sorted_ids(removed),
        custom=custom,
    )
    await publish(session, SkillProfileChanged(owner_id=owner_id))
    return await repo.get_skill_profile(session, owner_id), True


async def reset_skill_profile(
    session: AsyncSession, owner_id: uuid.UUID
) -> tuple[SkillProfile | None, bool]:
    """Reset to extracted: clear the ``added`` / ``removed`` / ``custom`` sets and publish
    ``SkillProfileChanged``. Returns ``(profile, changed)``; without a row or without edits
    nothing is written or published."""
    await repo.lock_owner_skill_profile(session, owner_id)
    profile = await repo.get_skill_profile(session, owner_id)
    if profile is None or not has_edits(profile):
        return profile, False
    await repo.save_skill_edits(session, owner_id, added=[], removed=[], custom=[])
    await publish(session, SkillProfileChanged(owner_id=owner_id))
    return await repo.get_skill_profile(session, owner_id), True


# --------------------------------------------------------------------------- Preferences

# The defaults a Student has before the first save (the same as the column defaults).
PREFERENCE_DEFAULTS: dict[str, Any] = {
    "role_titles": [],
    "locations": [],
    "job_types": [],
    "batch_year": None,
    "cgpa": None,
    "degree": None,
    "degree_other": None,
    "work_authorisation": None,
    "threshold": 60,
    "alert_frequency": "daily",
    "timezone": "Asia/Kolkata",
}
PREFERENCE_FIELDS: tuple[str, ...] = tuple(PREFERENCE_DEFAULTS)


def _cgpa(value: float | Decimal | None) -> Decimal | None:
    """CGPA as stored: numeric(4, 2)."""
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def preference_values(prefs: Preferences | None) -> dict[str, Any]:
    """The effective values of a stored row (the defaults when there is none). ``cgpa`` is
    a two-decimal ``Decimal``."""
    if prefs is None:
        return dict(PREFERENCE_DEFAULTS)
    values = {name: getattr(prefs, name) for name in PREFERENCE_FIELDS}
    values["role_titles"] = list(prefs.role_titles)
    values["locations"] = list(prefs.locations)
    values["job_types"] = list(prefs.job_types)
    values["cgpa"] = _cgpa(prefs.cgpa)
    return values


async def get_preferences(session: AsyncSession, owner_id: uuid.UUID) -> Preferences | None:
    return await repo.get_preferences(session, owner_id)


async def save_preferences(
    session: AsyncSession, owner_id: uuid.UUID, values: dict[str, Any]
) -> tuple[Preferences, bool]:
    """Replace the owner's Preferences with ``values`` (already validated and normalised by
    the API schema; every field present). Returns ``(prefs, changed)``.

    When nothing was stored yet, or the values differ from the stored ones, the row is
    upserted and ``PreferencesChanged`` is published in the caller's transaction. Otherwise
    nothing is written or published.
    """
    new = {name: values[name] for name in PREFERENCE_FIELDS}
    new["role_titles"] = list(new["role_titles"])
    new["locations"] = list(new["locations"])
    new["job_types"] = list(new["job_types"])
    new["cgpa"] = _cgpa(new["cgpa"])

    await repo.lock_owner_preferences(session, owner_id)
    stored = await repo.get_preferences(session, owner_id)
    if stored is not None and preference_values(stored) == new:
        return stored, False
    prefs = await repo.upsert_preferences(session, owner_id, new)
    await publish(session, PreferencesChanged(owner_id=owner_id))
    return prefs, True
