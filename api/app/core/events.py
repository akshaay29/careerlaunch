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


@dataclass(frozen=True)
class ResumeActivated(Event):
    """``resume_id`` became the owner's one active Resume (FR-3). Published in the same
    transaction that deactivated the previous one, so subscribers see the new state."""

    owner_id: uuid.UUID
    resume_id: uuid.UUID


@dataclass(frozen=True)
class SkillProfileChanged(Event):
    """The owner's Skill Profile changed (FR-4): a new extraction was stored, or the Student
    edited or reset it. Matching marks the owner's scores stale (Epic 3)."""

    owner_id: uuid.UUID


@dataclass(frozen=True)
class PreferencesChanged(Event):
    """The owner's Preferences changed (FR-5): the first save, or a save whose effective
    values differ from the stored ones. Collection (Epic 2) and matching (Epic 3) react."""

    owner_id: uuid.UUID


@dataclass(frozen=True)
class AccountDeleting(Event):
    """The owner's account is being deleted (FR-2, AD-17). Published inside the deletion
    transaction BEFORE the ``accounts`` row (and, by cascade, every Student-owned row) is
    deleted, so subscribers still see the owner's data and can cancel their own work."""

    owner_id: uuid.UUID


Handler = Callable[[AsyncSession, Any], Awaitable[None]]

_subscribers: dict[type[Event], list[Handler]] = {}


def subscribe[E: Event](
    event_type: type[E], handler: Callable[[AsyncSession, E], Awaitable[None]]
) -> None:
    """Register ``handler`` for ``event_type`` (exact type match). Order is registration order.

    Registering the same handler for the same event again is a no-op, so app wiring that
    runs more than once (one app per test) never makes a handler run twice.
    """
    if not (isinstance(event_type, type) and issubclass(event_type, Event)):
        raise TypeError("event_type must be an Event subclass")
    handlers = _subscribers.setdefault(event_type, [])
    if handler not in handlers:
        handlers.append(handler)


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
