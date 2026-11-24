"""tracker.events: the module's domain-event subscribers.

:func:`register` is called once by app wiring (``app.main.create_app``); registering again
is harmless (``core.events.subscribe`` ignores a handler that is already registered).

``ApplicationClosed`` and ``ApplicationReopened`` are published by ``tracker.service``;
outreach subscribes to them later.
"""

from app.core.events import AccountDeleting, subscribe
from app.modules.tracker import service


def register() -> None:
    # The owner's Applications go with the account (cascade): release their Listing pins.
    subscribe(AccountDeleting, service.on_account_deleting)
