"""accounts.tasks: job entry points (AD-4). Arguments are ids only.

Account deletion (FR-2, AD-17) commits the DB side in one transaction and enqueues two jobs
for what lives outside Postgres. Both are idempotent, so retries and duplicate runs are safe,
both record an ``ops_events`` row with counts only (no owner or entity id), and both stamp
their column on the account's tombstone when they succeed. The daily
``resume_pending_deletions`` job (a global ``schedules`` row) re-enqueues whatever is still
unstamped, so a deletion whose jobs used up their retries is still finished.
"""

import uuid

from app.core.db import get_sessionmaker
from app.core.jobs import DEFAULT_QUEUE, PRIORITY_QUEUE, RetryPolicy, task
from app.core.ops import record_event
from app.modules.accounts import repo
from app.ports.auth_admin import get_auth_admin
from app.ports.file_store import get_file_store

PURGE_FILES = "accounts.purge_files"
DELETE_AUTH_USER = "accounts.delete_auth_user"
RESUME_PENDING_DELETIONS = "accounts.resume_pending_deletions"

FILES_PURGED_EVENT = "account.files_purged"
AUTH_DELETED_EVENT = "account.auth_deleted"

# The account row is already gone when these run, so a job that gives up leaves personal data
# behind. 10 runs with waits of 4**n seconds (4 s up to 4**9 s, about 4 days in total) span a
# long Storage or Auth outage. After that, resume_pending_deletions re-enqueues them daily.
PURGE_RETRY = RetryPolicy(max_attempts=10, exponential_wait=4)


@task(PURGE_FILES, queue=PRIORITY_QUEUE, retry=PURGE_RETRY)
async def purge_files(*, owner_id: uuid.UUID) -> None:
    """Remove every stored object under ``<owner_id>/`` (Resumes, Documents, ...). A re-run
    finds nothing and deletes 0 objects. A Storage error raises, so the job is retried."""
    removed = await get_file_store().delete_prefix(f"{owner_id}/")
    async with get_sessionmaker()() as session:
        await repo.mark_files_purged(session, owner_id)
        await record_event(session, FILES_PURGED_EVENT, counts={"objects": removed})
        await session.commit()


@task(DELETE_AUTH_USER, queue=PRIORITY_QUEUE, retry=PURGE_RETRY)
async def delete_auth_user(*, owner_id: uuid.UUID) -> None:
    """Delete the Supabase Auth user (an already-deleted user counts as success). An Auth
    error raises, so the job is retried."""
    await get_auth_admin().delete_user(owner_id)
    async with get_sessionmaker()() as session:
        await repo.mark_auth_deleted(session, owner_id)
        await record_event(session, AUTH_DELETED_EVENT)
        await session.commit()


@task(RESUME_PENDING_DELETIONS, queue=DEFAULT_QUEUE)
async def resume_pending_deletions() -> None:
    """Backstop for the 30-day deletion guarantee (no owner ids: one global daily run).
    Re-enqueues the purge job(s) of every deletion older than an hour that has not finished.
    Same keys as the original jobs, so a run that is still pending dedupes it."""
    # Imported here: service imports this module for the task names.
    from app.modules.accounts import service

    async with get_sessionmaker()() as session:
        await service.resume_pending_deletions(session)
        await session.commit()
