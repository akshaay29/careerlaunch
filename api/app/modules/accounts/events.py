"""accounts.events: the module's domain-event subscribers.

:func:`register` is called once by app wiring (``app.main.create_app``); registering again
is harmless (``core.events.subscribe`` ignores a handler that is already registered).
"""

from app.core.events import ConsentRevoked, subscribe
from app.modules.accounts import service


def register() -> None:
    # Withdrawn provider_runs consent pauses the owner's collection schedules (AD-11).
    subscribe(ConsentRevoked, service.on_consent_revoked)
