"""listings.events: the module's domain-event subscribers.

:func:`register` is called once by app wiring (``app.main.create_app``); registering again
is harmless (``core.events.subscribe`` ignores a handler that is already registered).

``ListingsIngested`` is published by ``listings.service.ingest_drafts``; matching subscribes
to it in Epic 3.
"""

from app.core.events import ProviderKeyChanged, subscribe
from app.modules.listings import service


def register() -> None:
    # A saved Provider Key creates or resumes the daily collection schedule and enqueues
    # today's first run (Story 2.3).
    subscribe(ProviderKeyChanged, service.on_provider_key_changed)
