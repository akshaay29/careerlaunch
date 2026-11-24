"""matching.tasks: job entry points (AD-4). Arguments are ids only.

* ``matching.recompute`` (``owner_id``, ``priority``): re-scores one Student's candidate set
  and stores the scores (Story 3.2). Enqueued only by ``matching.service.mark_stale``: on the
  ``priority`` queue (``priority`` 1, key ``matching.recompute:student:<owner_id>:priority``)
  for the Student's own edits, on ``default`` (``priority`` 0, key
  ``matching.recompute:student:<owner_id>``) for new Listings. ``priority``
  only tells a run that must go again which queue to use. Idempotent.
"""

import uuid

from app.core.jobs import task

RECOMPUTE = "matching.recompute"


@task(RECOMPUTE)
async def recompute(*, owner_id: uuid.UUID, priority: int = 0) -> None:
    # Imported here: service imports this module for the task name.
    from app.modules.matching import service

    await service.recompute_for_owner(owner_id, priority=bool(priority))
