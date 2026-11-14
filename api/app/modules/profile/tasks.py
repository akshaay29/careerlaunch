"""profile.tasks: job entry points (AD-4). Arguments are ids only."""

import logging
import uuid

from app.core.db import get_sessionmaker
from app.core.jobs import PRIORITY_QUEUE, RetryPolicy, task
from app.modules.profile import extraction
from app.modules.profile.files import storage_path
from app.ports.file_store import get_file_store

logger = logging.getLogger("app.profile")

DELETE_STORED_FILES = "profile.delete_stored_files"
DELETE_DOCUMENT_FILE = "profile.delete_document_file"
EXTRACT_RESUME = "profile.extract_resume"

# The row is already gone when this runs, so if every attempt fails the private file is
# orphaned for good. The default policy (5 runs) gives up after about 6 minutes; 10 runs with
# waits of 4**n seconds (4 s up to 4**9 s, about 4 days in total) span a long Storage outage.
DELETE_RETRY = RetryPolicy(max_attempts=10, exponential_wait=4)


@task(DELETE_STORED_FILES, queue=PRIORITY_QUEUE, retry=DELETE_RETRY)
async def delete_stored_files(*, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    """Remove a deleted Resume's object. The path is derived from the ids (the row is
    already gone). Idempotent: deleting a missing object is not an error, so retries and
    duplicate runs are safe."""
    await get_file_store().delete([storage_path(owner_id, "resume", resume_id)])


@task(DELETE_DOCUMENT_FILE, queue=PRIORITY_QUEUE, retry=DELETE_RETRY)
async def delete_document_file(*, owner_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Remove a deleted Document's object (FR-26), like :func:`delete_stored_files`: the path
    is derived from the ids and the delete is idempotent."""
    await get_file_store().delete([storage_path(owner_id, "document", document_id)])


@task(EXTRACT_RESUME, queue=PRIORITY_QUEUE)
async def extract_resume(*, owner_id: uuid.UUID, resume_id: uuid.UUID) -> None:
    """Build the Skill Profile from the owner's active Resume (FR-4).

    * A no-op when the Resume is gone or no longer active (a stale job).
    * Storage errors raise, so the default retry policy applies.
    * A parser crash, timeout or limit stores ``failed`` and does NOT raise: a malformed file
      fails the same way every time, so it is never retried.
    * Idempotent: a re-run stores the same result for the same file.
    """
    # Imported here: service imports this module for the task names.
    from app.modules.profile import service

    ids = {"owner_id": str(owner_id), "resume_id": str(resume_id)}
    async with get_sessionmaker()() as session:
        target = await service.extraction_target(session, owner_id, resume_id)
    if target is None:
        logger.info("resume_extraction_skipped", extra=ids)
        return

    # Outside any transaction: no open DB transaction while downloading or parsing.
    data = await get_file_store().get(storage_path(owner_id, "resume", resume_id))
    result = await extraction.extract(data, target.content_type)
    if result.status == "failed":
        logger.warning("resume_extraction_failed", extra={**ids, "reason": result.reason})

    async with get_sessionmaker()() as session:
        stored = await service.save_extraction(session, owner_id, resume_id, result)
        await session.commit()
    logger.info(
        "resume_extraction_finished",
        extra={**ids, "status": result.status, "stored": stored, "skills": len(result.skills)},
    )
