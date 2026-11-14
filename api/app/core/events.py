"""Typed domain events (AD-20).

Events are frozen dataclasses. A module that must react to work done in a module to its RIGHT
(or in its own module) registers a handler with :func:`subscribe`; the publisher calls
``await publish(session, event)`` inside its own unit of work.

* Subscribers run synchronously (awaited one after another), in registration order, on the
  publisher's session, inside the publisher's transaction, before commit.
* If a subscriber raises, the exception propagates to the publisher, whose router or task
  then does not commit: the whole unit of work rolls back.
* ``publish`` never commits.

``core`` imports nothing from ``app.modules``; modules register their handlers at import time
(their ``events.py``).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Event:
    """Base for every domain event. Fields are ids and short machine names only."""


@dataclass(frozen=True)
class ConsentRevoked(Event):
    """A Student withdrew consent for ``purpose`` (AD-11). Owning modules cancel or pause
    their own work for that purpose in the same transaction."""

    owner_id: uuid.UUID
    purpose: str


Handler = Callable[[AsyncSession, Any], Awaitable[None]]

_subscribers: dict[type[Event], list[Handler]] = {}


def subscribe[E: Event](
    event_type: type[E], handler: Callable[[AsyncSession, E], Awaitable[None]]
) -> None:
    """Register ``handler`` for ``event_type`` (exact type match). Order is registration order."""
    if not (isinstance(event_type, type) and issubclass(event_type, Event)):
        raise TypeError("event_type must be an Event subclass")
    _subscribers.setdefault(event_type, []).append(handler)


async def publish(session: AsyncSession, event: Event) -> None:
    """Run every subscriber of ``type(event)`` on ``session``, in order. Never commits.

    An exception from a subscriber propagates unchanged; later subscribers do not run.
    """
    if not isinstance(event, Event):
        raise TypeError("publish() takes an Event instance")
    for handler in list(_subscribers.get(type(event), ())):
        await handler(session, event)


def clear_subscribers(event_type: type[Event] | None = None) -> None:
    """Test-only: drop the subscribers of ``event_type`` (or of every event)."""
    if event_type is None:
        _subscribers.clear()
    else:
        _subscribers.pop(event_type, None)
