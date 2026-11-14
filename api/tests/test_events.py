"""AD-20: core.events registry (typed, frozen events; ordered, synchronous subscribers)."""

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.core import events
from app.core.events import ConsentRevoked, Event, clear_subscribers, publish, subscribe


@pytest.fixture(autouse=True)
def isolated_registry() -> Iterator[None]:
    saved = {k: list(v) for k, v in events._subscribers.items()}
    clear_subscribers()
    yield
    clear_subscribers()
    events._subscribers.update(saved)


@dataclasses.dataclass(frozen=True)
class OtherEvent(Event):
    n: int


SESSION: Any = object()  # publish only passes the session through


def test_events_are_frozen() -> None:
    event = ConsentRevoked(owner_id=uuid.uuid4(), purpose="provider_runs")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.purpose = "pool_contribution"  # type: ignore[misc]


async def test_subscribers_run_in_registration_order_with_session() -> None:
    calls: list[tuple[str, object, object]] = []

    async def first(session: Any, event: ConsentRevoked) -> None:
        calls.append(("first", session, event))

    async def second(session: Any, event: ConsentRevoked) -> None:
        calls.append(("second", session, event))

    subscribe(ConsentRevoked, first)
    subscribe(ConsentRevoked, second)
    event = ConsentRevoked(owner_id=uuid.uuid4(), purpose="provider_runs")
    await publish(SESSION, event)
    assert calls == [("first", SESSION, event), ("second", SESSION, event)]


async def test_only_subscribers_of_that_type_run() -> None:
    seen: list[Event] = []

    async def on_other(_session: Any, event: OtherEvent) -> None:
        seen.append(event)

    subscribe(OtherEvent, on_other)
    await publish(SESSION, ConsentRevoked(owner_id=uuid.uuid4(), purpose="provider_runs"))
    assert seen == []
    await publish(SESSION, OtherEvent(n=1))
    assert seen == [OtherEvent(n=1)]


async def test_publish_without_subscribers_is_a_no_op() -> None:
    await publish(SESSION, OtherEvent(n=2))


async def test_failing_subscriber_propagates_and_stops_later_ones() -> None:
    later: list[int] = []

    async def boom(_session: Any, _event: OtherEvent) -> None:
        raise RuntimeError("subscriber failed")

    async def after(_session: Any, event: OtherEvent) -> None:
        later.append(event.n)

    subscribe(OtherEvent, boom)
    subscribe(OtherEvent, after)
    with pytest.raises(RuntimeError, match="subscriber failed"):
        await publish(SESSION, OtherEvent(n=3))
    assert later == []


async def test_clear_subscribers_by_type() -> None:
    seen: list[str] = []

    async def on_other(_session: Any, _event: OtherEvent) -> None:
        seen.append("other")

    async def on_revoked(_session: Any, _event: ConsentRevoked) -> None:
        seen.append("revoked")

    subscribe(OtherEvent, on_other)
    subscribe(ConsentRevoked, on_revoked)
    clear_subscribers(OtherEvent)
    await publish(SESSION, OtherEvent(n=4))
    await publish(SESSION, ConsentRevoked(owner_id=uuid.uuid4(), purpose="provider_runs"))
    assert seen == ["revoked"]


async def test_type_checks() -> None:
    async def handler(_session: Any, _event: Any) -> None:
        return None

    with pytest.raises(TypeError):
        subscribe(dict, handler)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await publish(SESSION, object())  # type: ignore[arg-type]
