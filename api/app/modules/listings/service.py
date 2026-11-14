"""listings.service: the single ingest pipeline (AD-3, AD-7) and browsing (FR-6, FR-7).

Takes the session; never commits (routers and tasks commit).

* :func:`ingest_draft` is the ONE way a Listing is written: upsert on
  ``(source, source_job_id)``, skills via ``core.skills``, ``search_doc`` built in SQL from
  bind parameters, the description discarded, visibility from ``pool_contribution`` consent.
* :func:`collect_and_ingest` runs one adapter through it and publishes one
  ``ListingsIngested`` per call.
* Browsing returns only Listings visible to the actor (pool, or private to them) that are
  not expired.
"""

import base64
import binascii
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import skills as core_skills
from app.core.errors import InvalidInputError, NotFoundError
from app.core import events as core_events
from app.core.events import ListingsIngested
from app.core.ops import record_event
from app.modules.accounts import service as accounts_service
from app.modules.accounts.consent_text import ConsentPurpose
from app.modules.listings import repo
from app.modules.listings.models import Listing
from app.ports.source_adapter import ListingDraft, SourceAdapter, SourceQuery

logger = logging.getLogger("app.listings")

NOT_FOUND_DETAIL = "No such Listing."
INVALID_CURSOR = "The cursor is not valid. Start again without a cursor."
INGESTED_EVENT = "listings.ingested"
SKIPPED_EVENT = "listings.collect_skipped"


class ListingIngestError(Exception):
    """Storing a draft failed in the database. The message names only the driver error
    class: the original error (which can carry the bind parameters, description included)
    is dropped so it can never reach a log."""


def _visible_to_collector(result: repo.UpsertResult, collected_by: uuid.UUID | None) -> bool:
    return result.visibility_owner_id is None or result.visibility_owner_id == collected_by


async def _upsert(
    session: AsyncSession,
    draft: ListingDraft,
    *,
    collected_by: uuid.UUID | None,
    contributes_to_pool: bool,
) -> repo.UpsertResult:
    if collected_by is None and not contributes_to_pool:
        raise ValueError("a System collection (collected_by None) always contributes to the pool")
    skill_ids = core_skills.sorted_ids(
        core_skills.normalize(f"{draft.title}\n{draft.description}")
    )
    try:
        return await repo.upsert_listing(
            session,
            source=draft.source,
            source_job_id=draft.source_job_id,
            url=draft.url,
            title=draft.title,
            company=draft.company_raw,
            location=draft.location_raw,
            job_type=draft.job_type,
            posted_at=draft.posted_at,
            deadline=draft.deadline,
            skills=skill_ids,
            skill_labels=" ".join(core_skills.label(s) for s in skill_ids),
            vocab_version=core_skills.VOCAB_VERSION,
            description=draft.description,
            collected_by=collected_by,
            contributes_to_pool=contributes_to_pool,
        )
    except DBAPIError as exc:
        # ``from None``: the DBAPIError's text includes the bind parameters.
        raise ListingIngestError(
            f"storing a Listing draft failed ({type(exc.orig).__name__})"
        ) from None


async def ingest_draft(
    session: AsyncSession,
    draft: ListingDraft,
    *,
    collected_by: uuid.UUID | None,
    contributes_to_pool: bool,
    publish: bool = True,
) -> tuple[uuid.UUID, bool]:
    """Store ``draft`` as a Listing in one upsert; returns ``(listing_id, created)``.

    ``collected_by`` is the collecting Student (None = System, e.g. the seed).
    ``contributes_to_pool`` is the caller's ``pool_contribution`` consent result: True puts a
    new row in the shared pool, False keeps it private to ``collected_by``. On conflict only
    an authorised writer (contributing, or the collector the row is private to) refreshes the
    content; anyone else only advances ``last_seen_at``. Visibility only widens.

    With ``publish`` (the default) publishes ``ListingsIngested((id,), collected_by)``
    when the row is visible to the collector. ``collect_and_ingest`` stores its drafts
    without per-draft events and publishes one batched event instead.

    The description is passed to Postgres only as a bind parameter and is never stored or
    logged.
    """
    result = await _upsert(
        session, draft, collected_by=collected_by, contributes_to_pool=contributes_to_pool
    )
    if publish and _visible_to_collector(result, collected_by):
        await core_events.publish(
            session,
            ListingsIngested(listing_ids=(result.listing_id,), collected_by=collected_by),
        )
    return result.listing_id, result.created


async def collect_and_ingest(
    session: AsyncSession,
    adapter: SourceAdapter,
    query: SourceQuery,
    *,
    collected_by: uuid.UUID | None,
) -> int:
    """Collect from ``adapter`` and ingest at most ``query.limit`` drafts; returns the number
    of Listings ingested that are visible to the collector.

    A disabled or unknown Source records ``listings.collect_skipped`` and returns 0 without
    calling the adapter. The collector's ``pool_contribution`` consent is read once (the
    System always contributes). Each draft is stored in its own savepoint: a draft the
    database refuses is skipped and counted as ``rejected``. Rows not visible to the
    collector (another Student's private Listing) are left out of the count and the event.
    One ``listings.ingested`` ops event (counts only) and, when anything visible was
    ingested, one ``ListingsIngested`` are written in the caller's transaction.
    """
    source = await repo.get_source(session, adapter.source)
    if source is None or not source.enabled:
        await record_event(
            session,
            SKIPPED_EVENT,
            owner_id=collected_by,
            counts={"unknown_source" if source is None else "disabled": 1},
        )
        return 0
    contributes = (
        True
        if collected_by is None
        else await accounts_service.has_consent(
            session, collected_by, ConsentPurpose.POOL_CONTRIBUTION
        )
    )
    ids: dict[uuid.UUID, None] = {}
    created = rejected = seen = 0
    async for draft in adapter.collect(query):
        seen += 1
        if draft.source != adapter.source:
            raise ValueError("an adapter yielded a draft for another source")
        try:
            async with session.begin_nested():
                result = await _upsert(
                    session, draft, collected_by=collected_by, contributes_to_pool=contributes
                )
        except ListingIngestError as exc:
            rejected += 1
            # Ids and the driver error class only; never draft text.
            logger.warning(
                "listing_draft_rejected",
                extra={
                    "source": draft.source,
                    "source_job_id": draft.source_job_id,
                    "error": str(exc),
                },
            )
        else:
            if _visible_to_collector(result, collected_by):
                ids[result.listing_id] = None
                created += result.created
        if seen >= query.limit:
            break  # never pull more than the limit from the Source
    if seen:
        await record_event(
            session,
            INGESTED_EVENT,
            owner_id=collected_by,
            counts={"listings": len(ids), "created": created, "rejected": rejected},
        )
    if ids:
        await core_events.publish(
            session, ListingsIngested(listing_ids=tuple(ids), collected_by=collected_by)
        )
    return len(ids)


# --------------------------------------------------------------------------- browsing


def encode_cursor(listing: Listing) -> str:
    raw = f"{listing.first_seen_at.isoformat()}|{listing.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """The ``(first_seen_at, id)`` keyset of an opaque cursor; 422 when it is not one."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, listing_id = raw.split("|")
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(listing_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInputError(["query", "cursor"], INVALID_CURSOR) from exc


@dataclass(frozen=True)
class BrowseItem:
    listing: Listing
    source_label: str


@dataclass(frozen=True)
class BrowsePage:
    items: list[BrowseItem]
    next_cursor: str | None


async def browse(
    session: AsyncSession, actor_id: uuid.UUID, *, limit: int, cursor: str | None
) -> BrowsePage:
    """Listings visible to ``actor_id`` (pool or private to them), not expired, newest first.
    ``next_cursor`` is set when more may follow."""
    after = decode_cursor(cursor) if cursor else None
    rows = await repo.browse_page(session, actor_id, limit=limit + 1, after=after)
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1][0]) if len(rows) > limit else None
    return BrowsePage([BrowseItem(listing, label) for listing, label in page], next_cursor)


async def ensure_source(session: AsyncSession, source_id: str, label: str) -> None:
    """Create the Source (enabled) or refresh its label (used by the local seed)."""
    await repo.upsert_source(session, source_id, label)


async def source_state(session: AsyncSession, source_id: str) -> str:
    """``"enabled"``, ``"disabled"`` or ``"missing"``."""
    source = await repo.get_source(session, source_id)
    if source is None:
        return "missing"
    return "enabled" if source.enabled else "disabled"


async def get_listing(
    session: AsyncSession, actor_id: uuid.UUID, listing_id: uuid.UUID
) -> BrowseItem:
    """One visible, unexpired Listing. Another Student's private Listing, an expired one and
    an unknown id all raise :class:`NotFoundError` (AD-5)."""
    row = await repo.get_visible(session, actor_id, listing_id)
    if row is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    return BrowseItem(row[0], row[1])
