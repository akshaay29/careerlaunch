"""Operational log (AD-18): ``ops_events`` is append-only and holds ids and integer counts only.

Every module writes through :func:`record_event`. It inserts on the caller's session and never
commits; the caller's unit of work decides. Never pass personal data, bodies or secrets: the
signature only admits ids, a short type name and integer counts.
"""

import json
import re
import uuid
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Kinds and entity types are short machine names, e.g. ``job.succeeded`` / ``accounts.purge_files``.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")
_COUNT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_INSERT = text(
    """
    INSERT INTO ops_events (id, kind, owner_id, entity_type, entity_id, counts)
    VALUES (:id, :kind, :owner_id, :entity_type, :entity_id, CAST(:counts AS jsonb))
    """
)


def _check_name(field: str, value: str) -> None:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise ValueError(f"{field} must be a short lower-case machine name, got {value!r}")


def _check_id(field: str, value: object) -> None:
    if value is not None and not isinstance(value, uuid.UUID):
        raise ValueError(f"{field} must be a uuid.UUID or None")


def _check_counts(counts: Mapping[str, int] | None) -> dict[str, int]:
    if counts is None:
        return {}
    checked: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(key, str) or not _COUNT_KEY_RE.fullmatch(key):
            raise ValueError(f"counts keys must be snake_case names, got {key!r}")
        # bool is an int subclass; it is not a count.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"counts[{key!r}] must be an int")
        checked[key] = value
    return checked


async def record_event(
    session: AsyncSession,
    kind: str,
    *,
    owner_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    counts: dict[str, int] | None = None,
) -> uuid.UUID:
    """Append one ``ops_events`` row on ``session`` (no commit). Returns the new id."""
    _check_name("kind", kind)
    if entity_type is not None:
        _check_name("entity_type", entity_type)
    _check_id("owner_id", owner_id)
    _check_id("entity_id", entity_id)
    payload = _check_counts(counts)

    event_id = uuid.uuid7()
    await session.execute(
        _INSERT,
        {
            "id": event_id,
            "kind": kind,
            "owner_id": owner_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "counts": json.dumps(payload),
        },
    )
    return event_id
