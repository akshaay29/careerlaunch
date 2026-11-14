"""listings.tasks: job entry points (AD-4). Arguments are ids only.

* ``listings.collect`` (``owner_id``): one collection run for a Student, scoped to their
  Preferences. Enqueued by the daily ``schedules`` row (``scheduler_tick``) and, on a saved
  Provider Key, as ``listings.collect:student:<owner>:<local_date>``.
* ``listings.poll_provider_run`` (``provider_run_id``): one poll of a running Apify run; it
  re-enqueues itself with backoff until the run ends. A transient Apify error raises, so the
  retry policy backs off; a failed actor run is never re-run.

* ``listings.reap_stale_runs`` (no ids; a global hourly ``schedules`` row from migration
  0013): fails runs stuck in ``starting`` or ``running`` (best-effort abort).

* Story 2.5, four daily global jobs (no ids; ``schedules`` rows from migration 0015, about
  03:00 UTC): ``listings.expire`` (past deadline, or unseen for enough runs), ``listings.purge``
  (deletes Listings expired longer than the grace period), ``listings.evict`` (trims the pool
  to its cap) and ``listings.quota_check`` (alerts near the database quota). A task that
  raises is retried by the jobs layer.

All are idempotent: the run's ``run_key`` and status make a repeat a no-op.
"""

import uuid

from app.core.jobs import RetryPolicy, task

COLLECT = "listings.collect"
POLL_PROVIDER_RUN = "listings.poll_provider_run"
REAP_STALE_RUNS = "listings.reap_stale_runs"
EXPIRE = "listings.expire"
PURGE = "listings.purge"
EVICT = "listings.evict"
QUOTA_CHECK = "listings.quota_check"

# A poll that keeps hitting a transient Apify error must outlast the run's 3-hour window,
# or the run would stay ``running`` with nobody polling it: 8 runs with waits of 4**n
# seconds (4 s up to 4**7 s, about 6 hours in total).
POLL_RETRY = RetryPolicy(max_attempts=8, exponential_wait=4)


@task(COLLECT)
async def collect(*, owner_id: uuid.UUID) -> None:
    # Imported here: service imports this module for the task names.
    from app.modules.listings import service

    await service.collect_for_owner(owner_id)


@task(POLL_PROVIDER_RUN, retry=POLL_RETRY)
async def poll_provider_run(*, provider_run_id: uuid.UUID) -> None:
    from app.modules.listings import service

    await service.poll_provider_run(provider_run_id)


@task(REAP_STALE_RUNS)
async def reap_stale_runs() -> None:
    from app.modules.listings import service

    await service.reap_stale_runs()


@task(EXPIRE)
async def expire() -> None:
    from app.modules.listings import service

    await service.expire_listings()


@task(PURGE)
async def purge() -> None:
    from app.modules.listings import service

    await service.purge_listings()


@task(EVICT)
async def evict() -> None:
    from app.modules.listings import service

    await service.evict_listings()


@task(QUOTA_CHECK)
async def quota_check() -> None:
    from app.modules.listings import service

    await service.check_quota()
