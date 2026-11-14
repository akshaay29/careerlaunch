"""profile.tasks: job entry points (AD-4). Arguments are ids only."""

import uuid

from app.core.jobs import PRIORITY_QUEUE, RetryPolicy, task
from app.modules.profile.files import storage_path
from app.ports.file_store import get_file_store

DELETE_STORED_FILES = "profile.delete_stored_files"

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
