"""profile.events: the module's domain-event subscribers.

:func:`register` is called once by app wiring (``app.main.create_app``), so registration is
explicit at startup. Registering again is harmless (``core.events.subscribe`` ignores a
handler that is already registered).
"""

from app.core.events import ResumeActivated, subscribe
from app.modules.profile import service


def register() -> None:
    # A newly active Resume: mark the Skill Profile pending and enqueue its extraction.
    subscribe(ResumeActivated, service.on_resume_activated)
