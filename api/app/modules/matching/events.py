"""matching.events: the module's domain-event subscribers (Story 3.2).

:func:`register` is called once by app wiring (``app.main.create_app``); registering again
is harmless (``core.events.subscribe`` ignores a handler that is already registered).

Every subscriber only calls ``matching.service.mark_stale`` (the one recompute trigger), on
the publisher's session, inside its transaction.
"""

from app.core.events import (
    ListingsIngested,
    PreferencesChanged,
    ResumeActivated,
    SkillProfileChanged,
    subscribe,
)
from app.modules.matching import service


def register() -> None:
    # The Student's own changes: the priority queue, drained after the response by the
    # router that committed them.
    subscribe(ResumeActivated, service.on_resume_activated)
    subscribe(SkillProfileChanged, service.on_skill_profile_changed)
    subscribe(PreferencesChanged, service.on_preferences_changed)
    # New Listings: the affected Students, on the default queue, once per ingestion call.
    subscribe(ListingsIngested, service.on_listings_ingested)
