"""listings.service: the single ingest pipeline (AD-3, AD-7) and browsing (FR-6, FR-7).

Takes the session; never commits (routers and tasks commit).

* :func:`ingest_draft` is the ONE way a Listing is written: upsert on
  ``(source, source_job_id)``, skills via ``core.skills``, ``search_doc`` built in SQL from
  bind parameters, the description discarded, visibility from ``pool_contribution`` consent.
* :func:`ingest_drafts` stores a batch through it (one savepoint per draft) and publishes one
  ``ListingsIngested`` per call; :func:`collect_and_ingest` runs one adapter through it.
* :func:`start_provider_run` is the ONLY caller of ``ProviderRunner.start_run`` (AD-12) and
  checks the spend caps before any spend; :func:`poll_provider_run` collects the run's
  results through :func:`ingest_drafts`. Apify refusing a Student run pauses the Student
  (``accounts.service.pause_provider``). :func:`provider_usage` is the Student's usage view.
* Browsing returns only Listings visible to the actor (pool, or private to them) that are
  not expired.
* Story 2.6: :func:`search` is keyword search over the same visible, non-expired pool
  (full-text plus trigram similarity); the query is reduced to plain word tokens first.
* Story 2.5: the daily jobs (:func:`expire_listings`, :func:`purge_listings`,
  :func:`evict_listings`, :func:`check_quota`), the hooks later epics use (:func:`pin`,
  :func:`unpin`, :func:`set_relevance`) and the owner's takedown (:func:`block`,
  :func:`unblock`; a blocked draft is never ingested).
"""

import base64
import binascii
import logging
import math
import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretStr
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events as core_events
from app.core import skills as core_skills
from app.core.clock import local_day, next_local_time, utcnow
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import (
    CredentialsUnavailableError,
    InvalidInputError,
    NotFoundError,
    ProviderKeyUnreadableError,
)
from app.core.events import ListingsIngested, ProviderKeyChanged
from app.core.jobs import enqueue, upsert_owner_schedule
from app.core.ops import record_audit, record_event
from app.modules.accounts import service as accounts_service
from app.modules.accounts.consent_text import ConsentPurpose
from app.modules.listings import repo
from app.modules.listings.models import Listing, ProviderRun
from app.modules.listings.tasks import COLLECT, POLL_PROVIDER_RUN
from app.modules.profile import service as profile_service
from app.ports.provider_runner import (
    TERMINAL_FAILURES,
    ProviderKeyInvalid,
    ProviderRejected,
    ProviderRunner,
    ProviderUnavailable,
    get_provider_runner,
)
from app.ports.source_adapter import (
    SOURCE_ID_PATTERN,
    ListingDraft,
    SourceAdapter,
    SourceQuery,
    get_provider_source,
)

logger = logging.getLogger("app.listings")

NOT_FOUND_DETAIL = "No such Listing."
INVALID_CURSOR = "The cursor is not valid. Start again without a cursor."
INGESTED_EVENT = "listings.ingested"
SKIPPED_EVENT = "listings.collect_skipped"


class ListingIngestError(Exception):
    """Storing a draft failed in the database. The message names only the driver error
    class: the original error (which can carry the bind parameters, description included)
    is dropped so it can never reach a log."""


class ListingBlockedError(Exception):
    """The draft matches a takedown (``listing_blocks``): it is never stored. Internal to the
    ingest functions, which turn it into a skipped (rejected) draft."""


DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(url: str) -> str:
    """The form a URL takedown matches on: lower-cased scheme and host, the port unless it is
    the scheme's default, no userinfo, query or fragment, and no trailing slash on the path.
    ``ValueError`` when the URL cannot be parsed."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    scheme = parts.scheme.lower()
    try:
        number = parts.port
    except ValueError:  # an out-of-range port: keep what was written
        number = None
    # The scheme's default port is the same URL without it.
    port = f":{number}" if number and number != DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{host}{port}{parts.path.rstrip('/')}"


def normalize_company(company: str) -> str:
    """The form a company takedown matches on: trimmed and case-folded."""
    return company.strip().casefold()


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
        # Shared with every ingest, exclusive for block/unblock: a takedown committed before
        # this point is seen by the check, one still running finishes after this upsert.
        await repo.lock_blocks(session, exclusive=False)
        if await repo.is_blocked(
            session,
            source=draft.source,
            source_job_id=draft.source_job_id,
            url=canonical_url(draft.url),
            company=normalize_company(draft.company_raw),
        ):
            raise ListingBlockedError
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
) -> tuple[uuid.UUID, bool] | None:
    """Store ``draft`` as a Listing in one upsert; returns ``(listing_id, created)``, or None
    when the draft matches a takedown (``block``): it is not stored and that is not an error.

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
    try:
        result = await _upsert(
            session, draft, collected_by=collected_by, contributes_to_pool=contributes_to_pool
        )
    except ListingBlockedError:
        return None
    if publish and _visible_to_collector(result, collected_by):
        await core_events.publish(
            session,
            ListingsIngested(listing_ids=(result.listing_id,), collected_by=collected_by),
        )
    return result.listing_id, result.created


@dataclass(frozen=True)
class IngestCounts:
    """What one ingest call stored: ``listings`` visible to the collector (inserted or
    refreshed), how many of them were ``created``, drafts the database ``rejected``, and
    drafts stored but ``not_visible`` to the collector (another Student's private row)."""

    listings: int
    created: int
    rejected: int
    not_visible: int = 0


async def ingest_drafts(
    session: AsyncSession,
    drafts: Iterable[ListingDraft],
    *,
    collected_by: uuid.UUID | None,
) -> IngestCounts:
    """Ingest a batch of drafts in the caller's transaction (used by ``collect_and_ingest``
    and by the provider-run poll).

    The collector's ``pool_contribution`` consent is read once (the System always
    contributes). Each draft is stored in its own savepoint: a draft the database refuses is
    skipped and counted as ``rejected``, and so is a draft that matches a takedown (``block``;
    it is not an error and is not logged as one). Rows not visible to the collector (another
    Student's private Listing) are left out of the count and the event. When there was at
    least one draft, one ``listings.ingested`` ops event (counts only) is written and, when anything
    visible was ingested, one ``ListingsIngested``.
    """
    contributes = (
        True
        if collected_by is None
        else await accounts_service.has_consent(
            session, collected_by, ConsentPurpose.POOL_CONTRIBUTION
        )
    )
    ids: dict[uuid.UUID, None] = {}
    created = rejected = seen = not_visible = 0
    for draft in drafts:
        seen += 1
        try:
            async with session.begin_nested():
                result = await _upsert(
                    session, draft, collected_by=collected_by, contributes_to_pool=contributes
                )
        except ListingBlockedError:
            rejected += 1
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
            else:
                not_visible += 1
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
    return IngestCounts(
        listings=len(ids), created=created, rejected=rejected, not_visible=not_visible
    )


async def collect_and_ingest(
    session: AsyncSession,
    adapter: SourceAdapter,
    query: SourceQuery,
    *,
    collected_by: uuid.UUID | None,
) -> int:
    """Collect at most ``query.limit`` drafts from ``adapter`` and ingest them with
    :func:`ingest_drafts`; returns the number of Listings ingested that are visible to the
    collector.

    A disabled or unknown Source records ``listings.collect_skipped`` and returns 0 without
    calling the adapter. A draft for another Source is a ``ValueError`` (nothing is stored).
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
    drafts: list[ListingDraft] = []
    async for draft in adapter.collect(query):
        if draft.source != adapter.source:
            raise ValueError("an adapter yielded a draft for another source")
        drafts.append(draft)
        if len(drafts) >= query.limit:
            break  # never pull more than the limit from the Source
    counts = await ingest_drafts(session, drafts, collected_by=collected_by)
    return counts.listings


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


# --------------------------------------------------------------------------- search (Story 2.6)

SEARCH_MAX_QUERY_CHARS = 100
SEARCH_MIN_QUERY_CHARS = 2
SEARCH_MAX_TOKENS = 8
SEARCH_MAX_TOKEN_CHARS = 40
SEARCH_MAX_OFFSET = 200
SEARCH_MAX_LIMIT = 50
INVALID_QUERY = (
    f"Enter at least {SEARCH_MIN_QUERY_CHARS} letters or digits to search for "
    f"(at most {SEARCH_MAX_QUERY_CHARS} characters)."
)


def search_tokens(query: str) -> list[str]:
    """The word tokens of a search query: Unicode-normalised (NFKC), lower-cased, split on
    anything that is not a letter or a digit; at most 8 tokens of at most 40 characters.
    ``web/src/lib/listings.ts`` mirrors this."""
    text = unicodedata.normalize("NFKC", query).lower()
    words = "".join(c if c.isalnum() else " " for c in text).split()
    return [w[:SEARCH_MAX_TOKEN_CHARS] for w in words[:SEARCH_MAX_TOKENS]]


@dataclass(frozen=True)
class SearchPage:
    items: list[BrowseItem]
    next_offset: int | None


async def search(
    session: AsyncSession, actor_id: uuid.UUID, *, query: str, limit: int, offset: int
) -> SearchPage:
    """Listings visible to ``actor_id``, not expired, that match ``query``, best match first.
    All tokens must match (as word prefixes); a near-miss spelling still matches through
    trigram similarity on the title and company. ``next_offset`` is set when more matches
    follow and are within the offset cap. Raises :class:`InvalidInputError` for a query with
    no usable token (including only stop words), one over 100 characters, a limit outside
    1 to 50, or an offset past the cap. The query text is not logged or stored (its length is)."""
    field = ["query", "q"]
    if len(query) > SEARCH_MAX_QUERY_CHARS:
        raise InvalidInputError(field, INVALID_QUERY)
    tokens = search_tokens(query)
    phrase = " ".join(tokens)
    if len(phrase) < SEARCH_MIN_QUERY_CHARS:
        raise InvalidInputError(field, INVALID_QUERY)
    if not 1 <= limit <= SEARCH_MAX_LIMIT:
        raise InvalidInputError(["query", "limit"], f"limit must be 1 to {SEARCH_MAX_LIMIT}.")
    if not 0 <= offset <= SEARCH_MAX_OFFSET:
        raise InvalidInputError(["query", "offset"], f"offset must be 0 to {SEARCH_MAX_OFFSET}.")
    # Only alphanumeric tokens reach the string, so it carries no tsquery syntax.
    tsquery = " & ".join(f"{t}:*" for t in tokens)
    # A query of only stop words ("the", "to be") has no term to search for.
    if not await repo.tsquery_has_lexemes(session, tsquery):
        raise InvalidInputError(field, INVALID_QUERY)
    logger.info("listings.search chars=%d tokens=%d", len(query), len(tokens))
    rows = await repo.search_page(
        session,
        actor_id,
        tsquery=tsquery,
        phrase=phrase,
        threshold=get_settings().search_trigram_threshold,
        limit=limit + 1,
        offset=offset,
    )
    page = rows[:limit]
    following = offset + limit
    next_offset = following if len(rows) > limit and following <= SEARCH_MAX_OFFSET else None
    return SearchPage([BrowseItem(listing, label) for listing, label in page], next_offset)


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


async def visible_items(
    session: AsyncSession, actor_id: uuid.UUID, listing_ids: Iterable[uuid.UUID]
) -> list[BrowseItem]:
    """The given Listings that are visible to ``actor_id`` and not expired (others are
    silently left out), with their Source labels, in no particular order. One query."""
    rows = await repo.get_visible_many(session, actor_id, sorted(set(listing_ids)))
    return [BrowseItem(listing, label) for listing, label in rows]


@dataclass(frozen=True)
class ScoringInput:
    """What the Match Score needs from one Listing (Story 3.1): never the description, which
    pool Listings do not keep. ``lexemes`` are the ``search_doc`` lexemes (English stems)."""

    title: str
    skills: tuple[str, ...]
    vocab_version: str
    lexemes: tuple[str, ...]


async def get_scoring_input(
    session: AsyncSession, actor_id: uuid.UUID, listing_id: uuid.UUID
) -> ScoringInput:
    """The scoring input of one visible, unexpired Listing, with the same visibility as
    :func:`get_listing`: anything else raises :class:`NotFoundError` (AD-5)."""
    row = await repo.get_visible_scoring_input(session, actor_id, listing_id)
    if row is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    title, skills, vocab_version, lexemes = row
    return _scoring_input(title, skills, vocab_version, lexemes)


def _scoring_input(
    title: str | None,
    skills: Iterable[str | None],
    vocab_version: str,
    lexemes: Iterable[str | None],
) -> ScoringInput:
    # Array elements may be NULL; drop them (and empty strings) so the scorer never sees one.
    return ScoringInput(
        title or "",
        tuple(sorted({s for s in skills if s})),
        vocab_version,
        tuple(sorted({lx for lx in lexemes if lx})),
    )


async def scoring_inputs(
    session: AsyncSession, actor_id: uuid.UUID, listing_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ScoringInput]:
    """``{listing_id: ScoringInput}`` of the given Listings visible to ``actor_id`` and not
    expired (Story 3.4), in one query; others (unknown, expired, private to someone else) are
    silently left out. The same visibility as :func:`get_scoring_input`."""
    rows = await repo.get_visible_scoring_inputs(session, actor_id, sorted(set(listing_ids)))
    return {
        listing_id: _scoring_input(title, skills, vocab_version, lexemes)
        for listing_id, title, skills, vocab_version, lexemes in rows
    }


@dataclass(frozen=True)
class CandidateInput:
    """One Listing of a Student's candidate set with its scoring input (Story 3.2)."""

    listing_id: uuid.UUID
    scoring: ScoringInput


def role_tsquery(role_titles: Iterable[str]) -> str | None:
    """A prefix tsquery matching a Listing title that has every word of at least one role
    title: ``(backend:*A & developer:*A) | (data:*A & analyst:*A)``. Built from
    :func:`search_tokens` only, so it carries no tsquery syntax. None without any token."""
    groups = []
    for role in role_titles:
        tokens = search_tokens(role)
        if tokens:
            groups.append("(" + " & ".join(f"{t}:*A" for t in tokens) + ")")
    return " | ".join(groups) or None


async def candidate_set(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    role_titles: Iterable[str],
    locations: Iterable[str],
    job_types: Iterable[str],
    cap: int,
) -> list[CandidateInput]:
    """A Student's Match Score candidate set (Story 3.2) with each Listing's scoring input, in
    one query: Listings visible to ``owner_id`` and not expired whose title matches a role
    title (prefix full text over the title, or trigram word similarity of at least
    ``SEARCH_TRIGRAM_THRESHOLD``), whose location contains one of ``locations`` (ignoring
    case) or is unknown, and whose job type is one of ``job_types`` or unknown. An empty list
    is no filter on that field; role titles with no word in them match no pool Listing. At
    most ``cap`` of them, newest first, plus every manual Listing private to the Student.
    Sorted by Listing id."""
    roles = [r for r in role_titles if r and r.strip()]
    phrases = [p for p in (" ".join(search_tokens(r)) for r in roles) if p]
    rows = await repo.candidate_scoring_inputs(
        session,
        owner_id,
        role_tsquery=role_tsquery(roles),
        role_phrases=phrases if roles else None,
        locations=[loc.strip() for loc in locations if loc and loc.strip()],
        job_types=[t for t in job_types if t],
        threshold=get_settings().search_trigram_threshold,
        cap=cap,
    )
    return [
        CandidateInput(listing_id, _scoring_input(title, skills, vocab_version, lexemes))
        for listing_id, title, skills, vocab_version, lexemes in rows
    ]


async def lock_existing(
    session: AsyncSession, listing_ids: Iterable[uuid.UUID], *, for_update: bool = False
) -> list[uuid.UUID]:
    """Lock the given Listings that still exist (in id order, until the transaction ends) and
    return their ids. Default ``FOR KEY SHARE``: they cannot be deleted while rows that
    reference them are written. ``for_update``: ``FOR NO KEY UPDATE``, as before writing
    their relevance."""
    return await repo.lock_existing(session, sorted(set(listing_ids)), for_update=for_update)


async def has_pool_listing(session: AsyncSession, listing_ids: Iterable[uuid.UUID]) -> bool:
    """Whether one of ``listing_ids`` is in the shared pool (not private to its collector)."""
    return await repo.has_pool_listing(session, list(listing_ids))


# --------------------------------------------------------------------------- provider runs (AD-12)
#
# ``start_provider_run``, ``poll_provider_run`` and ``reap_stale_runs`` open their own short
# transactions (and commit them) because a network call must never happen inside one: each
# step commits before Apify is called, and the result is written in a new transaction.

PURPOSE_JOBS = "jobs"
PURPOSES = (PURPOSE_JOBS,)
LINKEDIN = "linkedin"

RUN_STARTED_EVENT = "listings.provider_run_started"
RUN_REFUSED_EVENT = "listings.provider_run_refused"
RUN_FAILED_EVENT = "listings.provider_run_failed"
RUN_SUCCEEDED_EVENT = "listings.provider_run_succeeded"
RUN_ORPHANED_EVENT = "listings.provider_run_orphaned"
RUN_ENTITY = "provider_run"

POLL_FIRST_DELAY = timedelta(seconds=60)
POLL_MAX_DELAY = timedelta(minutes=10)
RUN_TIMEOUT = timedelta(hours=3)
# A ``starting`` row younger than this is another start in flight (a no-op for a caller);
# an older one crashed mid-start.
STARTING_CRASHED_AFTER = timedelta(minutes=10)
# The hourly reaper fails ``starting`` rows older than this, and ``running`` rows started
# longer ago than RUN_TIMEOUT + RUNNING_GRACE (their poll gave up).
REAP_STARTING_AFTER = timedelta(minutes=15)
REAP_RUNNING_GRACE = timedelta(hours=1)
REAP_BATCH = 200

_ACTIVE = ("starting", "running")


@dataclass(frozen=True)
class RunOutcome:
    """Where a provider run stands after a call: its row id, ``status`` and ``error_kind``."""

    run_id: uuid.UUID
    status: str
    error_kind: str | None = None


def _outcome(run_id: uuid.UUID, run: ProviderRun | None) -> RunOutcome:
    if run is None:
        return RunOutcome(run_id, "missing")
    return RunOutcome(run_id, run.status, run.error_kind)


def poll_delay(attempts: int) -> timedelta:
    """The wait before the next poll after ``attempts`` polls: 2 min, 4 min, 8 min, then 10
    min (the first poll runs 60 s after the start)."""
    seconds = POLL_FIRST_DELAY.total_seconds() * 2 ** min(max(attempts, 0), 10)
    return min(timedelta(seconds=seconds), POLL_MAX_DELAY)


def _clean_params(params: dict[str, Any]) -> dict[str, list[str]]:
    """Only the collection scope is kept (role titles, locations, job types)."""

    def strings(key: str) -> list[str]:
        values = params.get(key) or []
        if isinstance(values, str):
            values = [values]
        return [v.strip() for v in values if isinstance(v, str) and v.strip()]

    return {
        "role_titles": strings("role_titles"),
        "locations": strings("locations"),
        "job_types": [t for t in strings("job_types") if t in ("internship", "full_time")],
    }


def _query(params: dict[str, list[str]], limit: int) -> SourceQuery:
    return SourceQuery(
        role_titles=tuple(params["role_titles"]),
        locations=tuple(params["locations"]),
        job_types=tuple(params["job_types"]),  # type: ignore[arg-type]
        limit=limit,
    )


def month_start(now: datetime) -> datetime:
    """The first instant of ``now``'s UTC calendar month."""
    now = now.astimezone(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(now: datetime) -> datetime:
    """The first instant of the UTC calendar month after ``now``'s."""
    start = month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def estimate_usd(max_items: int) -> Decimal:
    """A run's estimated cost: ``max_items x APIFY_PRICE_PER_ITEM_USD``, to 4 decimals."""
    price = Decimal(str(get_settings().apify_price_per_item_usd))
    return (price * max_items).quantize(Decimal("0.0001"))


async def _refusal(
    session: AsyncSession,
    owner_id: uuid.UUID | None,
    actor: str | None,
    scope: dict[str, list[str]],
    *,
    estimate: Decimal,
    charge: Decimal,
    caps: accounts_service.SpendCapsView | None,
) -> str | None:
    """Why a run may not start now, or None. Checked in order: role titles to search for,
    ``provider_runs`` consent, ``provider_status`` (paused, credit exhausted), the platform
    kill switch, the Source, the Provider Key, the actor allowlist, then the caps (Story
    2.4): ``run_cap`` (the ``estimate`` is above the per-run cap), ``monthly_cap`` (this
    month's committed spend plus this run's ``charge`` is above the monthly cap) and
    ``platform_cap`` (the same over every Student, above ``PLATFORM_MONTHLY_CAP_USD``).
    The caller holds the owner's spend lock. The System seed run (no owner) skips the
    Student checks and the caps."""
    if not scope["role_titles"]:
        return "no_preferences"
    if owner_id is not None:
        if not await accounts_service.has_consent(
            session, owner_id, ConsentPurpose.PROVIDER_RUNS
        ):
            return "no_consent"
        status = await accounts_service.get_provider_status(session, owner_id)
        blocked = status.blocks_runs(utcnow())
        if blocked is not None:
            return blocked
    if not get_settings().provider_runs_enabled:
        return "disabled"
    source = await repo.get_source(session, LINKEDIN)
    if source is None or not source.enabled:
        return "source_disabled"
    if owner_id is not None:
        meta = await accounts_service.get_credential_meta(session, owner_id)
        if not meta.has_key:
            return "no_key"
    if actor is None:
        return "no_actor"
    if owner_id is None or caps is None:
        return None
    if estimate > caps.per_run_usd:
        return "run_cap"
    since = month_start(utcnow())
    own = await repo.month_spend(session, since, owner_id)
    if own.committed + charge > caps.monthly_usd:
        return "monthly_cap"
    platform_cap = get_settings().platform_monthly_cap_usd
    if platform_cap is not None:
        # Serialises every Student's start while a platform cap is set (lock order: run
        # key, owner, platform).
        await repo.lock_spend(session, "platform")
        everyone = await repo.month_spend(session, since)
        if everyone.committed + charge > Decimal(str(platform_cap)):
            return "platform_cap"
    return None


async def _abort(token: SecretStr | None, apify_run_id: str | None) -> None:
    """Best-effort abort so a run we gave up on stops billing; never raises."""
    if token is None or apify_run_id is None:
        return
    try:
        await get_provider_runner().abort_run(token, apify_run_id)
    except Exception as exc:
        logger.warning("provider_run_abort_failed", extra={"exc_type": type(exc).__name__})


async def _owner_token(owner_id: uuid.UUID | None) -> SecretStr | None:
    """The owner's token for a best-effort abort, or None when it cannot be read."""
    if owner_id is None:
        return None
    try:
        async with get_sessionmaker()() as session:
            return await accounts_service.decrypt_credential(session, owner_id)
    except Exception as exc:
        logger.warning("provider_token_unreadable", extra={"exc_type": type(exc).__name__})
        return None


@dataclass(frozen=True)
class Pause:
    """A pause to write with a failed run: the ``provider_status`` reason and, for exhausted
    credit, when it ends."""

    reason: accounts_service.PauseReason
    exhausted_until: datetime | None = None


_REJECTION_REASONS: dict[str, accounts_service.PauseReason] = {
    "credit": "credit_exhausted",
    "permission": "not_permitted",
    "blocked": "account_blocked",
}


async def _pause_for(
    owner_id: uuid.UUID | None, error: Exception, token: SecretStr | None
) -> Pause | None:
    """The pause an Apify refusal of a Student run calls for (None for the seed run). For
    exhausted credit the end of the account's billing cycle is read from Apify (no
    transaction may be open); unknown or not in the future, it is the next UTC month."""
    if owner_id is None:
        return None
    if isinstance(error, ProviderKeyInvalid):
        return Pause("key_invalid")
    if not isinstance(error, ProviderRejected):
        return None
    reason = _REJECTION_REASONS.get(error.kind, "account_blocked")
    if reason != "credit_exhausted":
        return Pause(reason)
    until = await _cycle_end(get_provider_runner(), token)
    now = utcnow()
    if until is None or until <= now:
        until = next_month_start(now)
    return Pause(reason, until)


async def _cycle_end(runner: ProviderRunner, token: SecretStr | None) -> datetime | None:
    if token is None:
        return None
    try:
        return await runner.billing_cycle_end(token)
    except Exception as exc:  # the port never raises; a bug must not stop the pause
        logger.warning("provider_cycle_end_failed", extra={"exc_type": type(exc).__name__})
        return None


async def _fail_run(
    run_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    error_kind: str,
    *,
    cost_usd: float | None = None,
    only: tuple[str, ...] = _ACTIVE,
    pause: Pause | None = None,
) -> RunOutcome:
    """Mark a row in one of the ``only`` states ``failed`` (in its own transaction) and record
    ``listings.provider_run_failed``. A known ``cost_usd`` is stored; otherwise the last one
    recorded is kept. Never starts a replacement run. A ``pause`` (Student runs only) is
    written in the same transaction, even when the row had already moved on (Apify's answer
    holds either way), unless the owner's key was saved after the run was created."""
    async with get_sessionmaker()() as session:
        run = await repo.get_run(session, run_id, for_update=True)
        paused = False
        if (
            run is not None
            and pause is not None
            and run.owner_id is not None
            # A rejection of an old key must not pause a Student who has since saved a new one.
            and not await accounts_service.credential_replaced_since(
                session, run.owner_id, run.created_at
            )
        ):
            await accounts_service.pause_provider(
                session, run.owner_id, pause.reason, pause.exhausted_until
            )
            paused = True
        if run is None or run.status not in only:
            if paused:
                await session.commit()
            return _outcome(run_id, run)
        values: dict[str, Any] = {
            "status": "failed",
            "error_kind": error_kind,
            "finished_at": utcnow(),
        }
        if cost_usd is not None:
            values["cost_usd"] = Decimal(str(cost_usd))
        await repo.update_run(session, run_id, **values)
        await record_event(
            session,
            RUN_FAILED_EVENT,
            owner_id=owner_id,
            entity_type=RUN_ENTITY,
            entity_id=run_id,
            counts={error_kind: 1},
        )
        await session.commit()
    logger.info("provider_run_failed", extra={"run_id": str(run_id), "error_kind": error_kind})
    return RunOutcome(run_id, "failed", error_kind)


async def start_provider_run(
    owner_id: uuid.UUID | None,
    purpose: str,
    params: dict[str, Any],
    *,
    run_key: str,
    token: SecretStr | None = None,
    max_items: int | None = None,
    max_charge_usd: float | None = None,
) -> RunOutcome:
    """THE Apify spend path (AD-12): the only caller of ``ProviderRunner.start_run``.

    ``owner_id`` is the Student whose Provider Key pays, or None for the System seed run,
    which passes the owner's ``token`` (no other caller passes one) and may set
    ``max_charge_usd``. A Student run's ``max_charge_usd`` is the Student's per-run cap and
    its estimate ``max_items x APIFY_PRICE_PER_ITEM_USD``; both are stored on the row.
    ``run_key`` is unique:

    1. In a short transaction (serialised per ``run_key``): a row that already has an Apify
       run id, or that failed or succeeded, is a no-op. A ``starting`` row without a run id
       is another start in flight (a no-op) while younger than
       :data:`STARTING_CRASHED_AFTER`; an older one crashed after the call may have started a
       run, becomes ``failed``/``start_unknown`` and is never restarted. Otherwise the scope
       (role titles), consent, ``provider_status``, the kill switch, the Source, the key,
       the actor allowlist and (under the owner's spend lock) the caps are checked: a
       failure stores the row as ``refused`` with its
       ``error_kind`` (a later call for the same key checks again); success stores it as
       ``starting``. Then commit.
    2. With no transaction open, the key is decrypted and the first allowlisted actor is
       started with its mapped input and the caps (``COLLECT_MAX_ITEMS``,
       ``COLLECT_MAX_CHARGE_USD``).
    3. In a new transaction, under the row lock, the run id is stored (``running``) only if
       the row is still ``starting`` without a run id, and for a Student run the first
       ``listings.poll_provider_run`` is enqueued 60 s out. Otherwise (the row was failed
       meanwhile) the started run is an orphan: it is aborted (best effort) and
       ``listings.provider_run_orphaned`` is recorded. A start error marks the row
       ``failed`` with its ``error_kind`` instead (never retried: the next scheduled run
       collects again); an invalid key or an Apify refusal also pauses the Student.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown provider-run purpose {purpose!r}")
    if (owner_id is None) != (token is not None):
        raise ValueError("only the System seed run (no owner) passes its own token")
    if owner_id is not None and max_charge_usd is not None:
        raise ValueError("a Student run is charged at most the Student's per-run cap")
    settings = get_settings()
    actors = settings.apify_actors
    actor = actors[0] if actors else None
    limit = max_items if max_items is not None else settings.collect_max_items
    if not 1 <= limit <= 1000:
        raise ValueError("max_items must be 1..1000")
    if max_charge_usd is not None and not 0 < max_charge_usd <= 50:
        raise ValueError("max_charge_usd must be > 0 and <= 50")
    scope = _clean_params(params)
    estimate = estimate_usd(limit)

    # 1. Commit the row before any network call.
    async with get_sessionmaker()() as session:
        await repo.lock_run_key(session, run_key)
        existing = await repo.get_run_by_key(session, run_key)
        if existing is not None and existing.status != "refused":
            if existing.status == "starting" and existing.apify_run_id is None:
                if utcnow() - existing.created_at < STARTING_CRASHED_AFTER:
                    return _outcome(existing.id, existing)  # another start is in flight
                existing_id = existing.id
                await session.rollback()
                return await _fail_run(
                    existing_id, owner_id, "start_unknown", only=("starting",)
                )
            return _outcome(existing.id, existing)
        caps: accounts_service.SpendCapsView | None = None
        if owner_id is None:
            seed_charge = (
                max_charge_usd if max_charge_usd is not None else settings.collect_max_charge_usd
            )
            charge_usd = Decimal(str(seed_charge))
        else:
            # Every start of this owner's runs is serialised here, so two runs can never
            # together pass the monthly cap.
            await repo.lock_spend(session, f"owner:{owner_id}")
            caps = await accounts_service.get_spend_caps(session, owner_id)
            charge_usd = caps.per_run_usd
        refusal = await _refusal(
            session, owner_id, actor, scope, estimate=estimate, charge=charge_usd, caps=caps
        )
        values: dict[str, Any] = {
            "status": "refused" if refusal else "starting",
            "error_kind": refusal,
            "actor": actor,
            "params": scope,
            "max_items": limit,
            "max_charge_usd": charge_usd,
            "estimated_cost_usd": estimate,
            "finished_at": utcnow() if refusal else None,
        }
        if existing is None:
            run = await repo.insert_run(
                session, owner_id=owner_id, purpose=purpose, run_key=run_key, **values
            )
            run_id = run.id
        else:
            run_id = existing.id
            # A fresh ``created_at``: the in-flight window counts from this start.
            await repo.update_run(session, run_id, created_at=utcnow(), **values)
        if refusal:
            await record_event(
                session,
                RUN_REFUSED_EVENT,
                owner_id=owner_id,
                entity_type=RUN_ENTITY,
                entity_id=run_id,
                counts={refusal: 1},
            )
        await session.commit()
    if refusal:
        return RunOutcome(run_id, "refused", refusal)
    if actor is None:  # _refusal returns "no_actor" first; kept explicit for type checkers
        raise RuntimeError("no allowlisted actor")
    charge = float(charge_usd)

    # 2. No transaction open from here until the result is written.
    try:
        if token is None:
            if owner_id is None:
                raise RuntimeError("a Student run needs its owner")
            try:
                async with get_sessionmaker()() as session:
                    token = await accounts_service.decrypt_credential(session, owner_id)
            except ProviderKeyUnreadableError:
                return await _fail_run(run_id, owner_id, "key_unreadable")
            except CredentialsUnavailableError:
                return await _fail_run(run_id, owner_id, "credentials_unavailable")
            if token is None:
                return await _fail_run(run_id, owner_id, "no_key")
        try:
            run_input = get_provider_source().run_input(actor, _query(scope, limit))
        except ValueError:
            return await _fail_run(run_id, owner_id, "bad_input")
        apify_run_id = await get_provider_runner().start_run(
            token, actor, run_input, max_items=limit, max_charge_usd=charge
        )
    except ProviderKeyInvalid as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(run_id, owner_id, "key_invalid", pause=pause)
    except ProviderRejected as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(run_id, owner_id, "rejected", pause=pause)
    except ProviderUnavailable:
        return await _fail_run(run_id, owner_id, "unavailable")
    except ValueError:
        return await _fail_run(run_id, owner_id, "bad_input")
    except Exception as exc:
        # The class name only: an exception message could carry request details.
        logger.warning("provider_run_start_error", extra={"exc_type": type(exc).__name__})
        return await _fail_run(run_id, owner_id, "start_error")

    # 3. Store the run id and schedule the first poll, unless the row moved on meanwhile.
    async with get_sessionmaker()() as session:
        locked = await repo.get_run(session, run_id, for_update=True)
        if locked is None or locked.status != "starting" or locked.apify_run_id is not None:
            await record_event(
                session,
                RUN_ORPHANED_EVENT,
                owner_id=owner_id if locked is not None else None,
                entity_type=RUN_ENTITY,
                entity_id=run_id if locked is not None else None,
            )
            await session.commit()
            orphaned = True
        else:
            orphaned = False
            await repo.update_run(
                session, run_id, status="running", apify_run_id=apify_run_id, started_at=utcnow()
            )
            if owner_id is not None:
                await enqueue(
                    session,
                    POLL_PROVIDER_RUN,
                    f"{POLL_PROVIDER_RUN}:{RUN_ENTITY}:{run_id}",
                    run_at=utcnow() + POLL_FIRST_DELAY,
                    provider_run_id=run_id,
                )
            await record_event(
                session,
                RUN_STARTED_EVENT,
                owner_id=owner_id,
                entity_type=RUN_ENTITY,
                entity_id=run_id,
            )
            await session.commit()
    if orphaned:
        logger.warning("provider_run_orphaned", extra={"run_id": str(run_id)})
        await _abort(token, apify_run_id)
        async with get_sessionmaker()() as session:
            return _outcome(run_id, await repo.get_run(session, run_id))
    return RunOutcome(run_id, "running")


async def poll_provider_run(
    run_id: uuid.UUID, *, token: SecretStr | None = None, reschedule: bool = True
) -> RunOutcome:
    """One poll of a ``running`` provider run (the ``listings.poll_provider_run`` job; the
    seed script calls it with the seed ``token`` and ``reschedule=False``). Anything not
    ``running`` is a no-op. Apify is called with no transaction open.

    * READY / RUNNING: ``poll_attempts`` + 1, the cost so far is kept and, with
      ``reschedule``, the next poll is enqueued (key scoped by attempt) after
      :func:`poll_delay`. Past :data:`RUN_TIMEOUT` since the start the run is aborted (best
      effort) and ``failed``/``timeout`` instead.
    * SUCCEEDED: at most ``max_items`` items are fetched and mapped, then in one transaction
      :func:`ingest_drafts` stores them and the cost, counts and ``succeeded`` are recorded
      with a ``listings.provider_run_succeeded`` ops event (counts only). Counts satisfy
      ``items == ingested + rejected + duplicates + not_visible``.
    * FAILED / ABORTED / TIMED-OUT: ``failed`` with that status as ``error_kind`` and the
      cost; never re-run.
    * ``ProviderUnavailable`` propagates (the job retries); a rejected or invalid key, a
      missing or unreadable key, or no master key fails the run (keeping the last known cost).
      An invalid key or a rejection also pauses the Student (seed runs never pause).
    """
    async with get_sessionmaker()() as session:
        run = await repo.get_run(session, run_id)
        if run is None or run.status != "running":
            return _outcome(run_id, run)
        owner_id, actor, apify_run_id = run.owner_id, run.actor, run.apify_run_id
        max_items, started_at = run.max_items, run.started_at
        if token is None:
            if owner_id is None:
                raise ValueError("the System seed run is polled by its script with its token")
            failure: str | None = None
            try:
                token = await accounts_service.decrypt_credential(session, owner_id)
            except ProviderKeyUnreadableError:
                failure = "key_unreadable"
            except CredentialsUnavailableError:
                failure = "credentials_unavailable"
            else:
                failure = "no_key" if token is None else None
            if failure is not None:
                await session.rollback()
                return await _fail_run(run_id, owner_id, failure)
    if token is None or actor is None or apify_run_id is None:
        raise RuntimeError("a running provider run has an actor, a run id and a token")

    runner = get_provider_runner()
    try:
        state = await runner.get_run(token, apify_run_id)
    except ProviderKeyInvalid as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(run_id, owner_id, "key_invalid", pause=pause)
    except ProviderRejected as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(run_id, owner_id, "rejected", pause=pause)

    if state.status in ("READY", "RUNNING"):
        now = utcnow()
        if started_at is not None and now - started_at >= RUN_TIMEOUT:
            await _abort(token, apify_run_id)
            return await _fail_run(run_id, owner_id, "timeout", cost_usd=state.usage_usd)
        async with get_sessionmaker()() as session:
            locked = await repo.get_run(session, run_id, for_update=True)
            if locked is None or locked.status != "running":
                return _outcome(run_id, locked)
            attempts = locked.poll_attempts + 1
            values: dict[str, Any] = {"poll_attempts": attempts}
            if state.usage_usd is not None:
                values["cost_usd"] = Decimal(str(state.usage_usd))  # last known cost
            await repo.update_run(session, run_id, **values)
            if reschedule:
                await enqueue(
                    session,
                    POLL_PROVIDER_RUN,
                    f"{POLL_PROVIDER_RUN}:{RUN_ENTITY}:{run_id}:{attempts}",
                    run_at=now + poll_delay(attempts),
                    provider_run_id=run_id,
                )
            await session.commit()
        return RunOutcome(run_id, "running")

    if state.status in TERMINAL_FAILURES:
        kind = state.status.lower().replace("-", "_")
        return await _fail_run(run_id, owner_id, kind, cost_usd=state.usage_usd)

    # SUCCEEDED
    if state.dataset_id is None:
        return await _fail_run(run_id, owner_id, "no_dataset", cost_usd=state.usage_usd)
    try:
        items = await runner.fetch_items(token, state.dataset_id, limit=max_items)
    except ProviderKeyInvalid as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(
            run_id, owner_id, "key_invalid", cost_usd=state.usage_usd, pause=pause
        )
    except ProviderRejected as exc:
        pause = await _pause_for(owner_id, exc, token)
        return await _fail_run(
            run_id, owner_id, "rejected", cost_usd=state.usage_usd, pause=pause
        )
    items = items[:max_items]
    item_count = len(items)
    drafts, bad_items = get_provider_source().drafts_from_items(actor, items)
    del items  # raw provider output is never stored
    duplicates = item_count - len(drafts) - bad_items

    async with get_sessionmaker()() as session:
        locked = await repo.get_run(session, run_id, for_update=True)
        if locked is None or locked.status != "running":
            return _outcome(run_id, locked)
        counts = await ingest_drafts(session, drafts, collected_by=owner_id)
        tallies = {
            "items": item_count,
            "ingested": counts.listings,
            "rejected": bad_items + counts.rejected,
            "duplicates": duplicates,
            "not_visible": counts.not_visible,
        }
        await repo.update_run(
            session,
            run_id,
            status="succeeded",
            dataset_id=state.dataset_id,
            cost_usd=Decimal(str(state.usage_usd)) if state.usage_usd is not None else None,
            finished_at=utcnow(),
            **tallies,
        )
        await record_event(
            session,
            RUN_SUCCEEDED_EVENT,
            owner_id=owner_id,
            entity_type=RUN_ENTITY,
            entity_id=run_id,
            counts=tallies,
        )
        await session.commit()
    return RunOutcome(run_id, "succeeded")


async def reap_stale_runs() -> int:
    """The hourly ``listings.reap_stale_runs`` job: runs nobody will finish are failed so
    they never stay stuck. ``starting`` rows older than :data:`REAP_STARTING_AFTER` become
    ``failed``/``start_unknown``; ``running`` rows started more than ``RUN_TIMEOUT +
    REAP_RUNNING_GRACE`` ago (their poll gave up) are aborted (best effort) and become
    ``failed``/``poll_exhausted``. Returns the number of rows failed."""
    now = utcnow()
    async with get_sessionmaker()() as session:
        starting = await repo.stale_runs(
            session, "starting", now - REAP_STARTING_AFTER, REAP_BATCH
        )
        running = await repo.stale_runs(
            session, "running", now - RUN_TIMEOUT - REAP_RUNNING_GRACE, REAP_BATCH
        )
    reaped = 0
    for run_id, owner_id, _ in starting:
        outcome = await _fail_run(run_id, owner_id, "start_unknown", only=("starting",))
        reaped += outcome.error_kind == "start_unknown"
    for run_id, owner_id, apify_run_id in running:
        await _abort(await _owner_token(owner_id), apify_run_id)
        outcome = await _fail_run(run_id, owner_id, "poll_exhausted", only=("running",))
        reaped += outcome.error_kind == "poll_exhausted"
    return reaped


async def get_provider_run(session: AsyncSession, run_id: uuid.UUID) -> ProviderRun | None:
    """One provider run row (ids, caps, counts and cost only)."""
    return await repo.get_run(session, run_id)


# --------------------------------------------------------------------------- usage (Story 2.4)

USAGE_RUNS = 30


@dataclass(frozen=True)
class ProviderUsage:
    """What the Student sees about their collection spend: their status as it stands now,
    their caps, this UTC month's spend, and their latest runs (the rows carry ids and params
    the router never returns)."""

    status: accounts_service.ProviderStatusView
    caps: accounts_service.SpendCapsView
    month: repo.MonthSpend
    runs: list[ProviderRun]


async def provider_usage(session: AsyncSession, owner_id: uuid.UUID) -> ProviderUsage:
    now = utcnow()
    status = await accounts_service.get_provider_status(session, owner_id)
    return ProviderUsage(
        status=status.effective(now),
        caps=await accounts_service.get_spend_caps(session, owner_id),
        month=await repo.month_spend(session, month_start(now), owner_id),
        runs=await repo.latest_runs(session, owner_id, USAGE_RUNS),
    )


# --------------------------------------------------------------------------- maintenance (2.5)

MAINTENANCE_BATCH = 500
EXPIRED_EVENT = "listings.expired"
PURGED_EVENT = "listings.purged"
EVICTED_EVENT = "listings.evicted"
QUOTA_ALERT_EVENT = "listings.quota_alert"
BLOCKED_EVENT = "listings.blocked"
UNBLOCKED_EVENT = "listings.unblocked"
BLOCK_ACTION = "listings.block"
UNBLOCK_ACTION = "listings.unblock"
MIB = 1024 * 1024

BLOCK_KINDS = ("listing", "url", "company")
MAX_BLOCK_VALUE_CHARS = 2048  # the listing_blocks.value CHECK
# How long block/unblock wait for ingests in flight before giving up (they hold up every
# ingest queued behind them while they wait).
BLOCK_LOCK_TIMEOUT_MS = 30_000
MAX_RELEVANCE = 100.0


async def _batches(step: Any) -> int:
    """Run ``step(session) -> int`` in its own committed transaction until it changes
    nothing; returns the total."""
    total = 0
    while True:
        async with get_sessionmaker()() as session:
            changed = await step(session)
            await session.commit()
        if changed == 0:
            return total
        total += changed


async def _record(kind: str, counts: dict[str, int]) -> None:
    async with get_sessionmaker()() as session:
        await record_event(session, kind, counts=counts)
        await session.commit()


async def expire_listings(*, batch: int = MAINTENANCE_BATCH) -> int:
    """The daily ``listings.expire`` job. Marks active Listings expired when their deadline
    is before today, or when ``linkedin`` ones were last seen more than ``LISTING_UNSEEN_DAYS``
    days ago and at least ``LISTING_UNSEEN_RUNS`` provider runs have succeeded since (without
    enough runs an idle pool never empties itself). Idempotent; counts only in the
    ``listings.expired`` event. Returns the number expired."""
    settings = get_settings()
    by_deadline = by_unseen = 0

    async def step(session: AsyncSession) -> int:
        nonlocal by_deadline, by_unseen
        deadline, unseen = await repo.expire_batch(
            session,
            unseen_days=settings.listing_unseen_days,
            unseen_runs=settings.listing_unseen_runs,
            batch=batch,
        )
        by_deadline += deadline
        by_unseen += unseen
        return deadline + unseen

    await _batches(step)
    await _record(EXPIRED_EVENT, {"deadline": by_deadline, "unseen": by_unseen})
    return by_deadline + by_unseen


async def purge_listings(*, batch: int = MAINTENANCE_BATCH) -> int:
    """The daily ``listings.purge`` job: deletes Listings expired longer than
    ``LISTING_PURGE_GRACE_DAYS`` ago, never a pinned one. Returns the number deleted."""
    grace = get_settings().listing_purge_grace_days

    async def step(session: AsyncSession) -> int:
        return await repo.purge_batch(session, grace_days=grace, batch=batch)

    purged = await _batches(step)
    await _record(PURGED_EVENT, {"purged": purged})
    return purged


async def evict_listings(*, batch: int = MAINTENANCE_BATCH) -> int:
    """The daily ``listings.evict`` job: when active Listings exceed ``LISTING_POOL_CAP``,
    deletes the excess (lowest relevance, then oldest ``last_seen_at``), never a pinned
    Listing, even when the cap cannot be reached. Returns the number deleted."""
    cap = get_settings().listing_pool_cap
    async with get_sessionmaker()() as session:
        active = await repo.count_active(session)
    excess = active - cap
    removed = 0
    while removed < excess:
        async with get_sessionmaker()() as session:
            changed = await repo.evict_batch(session, batch=min(batch, excess - removed))
            await session.commit()
        if changed == 0:
            break  # only pinned or locked rows are left
        removed += changed
    await _record(EVICTED_EVENT, {"removed": removed, "active": active, "cap": cap})
    return removed


async def check_quota() -> bool:
    """The daily ``listings.quota_check`` job: at or above ``DB_QUOTA_ALERT_PERCENT`` percent
    of ``DB_QUOTA_BYTES`` records ``listings.quota_alert`` (counts: ``used_mb``, ``quota_mb``,
    ``percent``) and logs a warning. Returns whether it alerted."""
    settings = get_settings()
    async with get_sessionmaker()() as session:
        used = await repo.database_size_bytes(session)
    quota = settings.db_quota_bytes
    if used * 100 < settings.db_quota_alert_percent * quota:
        return False
    counts = {"used_mb": used // MIB, "quota_mb": quota // MIB, "percent": used * 100 // quota}
    logger.warning("listing_quota_alert", extra=counts)
    await _record(QUOTA_ALERT_EVENT, counts)
    return True


# ---- hooks for later epics (they take the session and never commit)


async def _locked(session: AsyncSession, listing_id: uuid.UUID) -> Listing:
    listing = await repo.lock_listing(session, listing_id)
    if listing is None:
        raise NotFoundError(NOT_FOUND_DETAIL)
    return listing


async def pin(session: AsyncSession, listing_id: uuid.UUID) -> int:
    """Protect a Listing from purge and eviction (a tracker holds it). Also clears
    ``expired_at``, so it stays visible. Returns the new ``pin_count``; an unknown id raises
    :class:`NotFoundError`."""
    listing = await _locked(session, listing_id)
    count = listing.pin_count + 1
    await repo.set_pin_count(session, listing_id, count)
    return count


async def unpin(session: AsyncSession, listing_id: uuid.UUID) -> int:
    """Release one pin (never below 0); returns the new ``pin_count``."""
    listing = await _locked(session, listing_id)
    count = max(listing.pin_count - 1, 0)
    await repo.set_pin_count(session, listing_id, count)
    return count


async def set_relevance(session: AsyncSession, listing_id: uuid.UUID, relevance: float) -> None:
    """Rank a Listing for eviction (higher survives longer). ``relevance`` must be finite and
    within 0..100 (``ValueError``)."""
    if isinstance(relevance, bool) or not isinstance(relevance, int | float):
        raise ValueError("relevance must be a number")
    try:
        valid = math.isfinite(relevance) and 0 <= relevance <= MAX_RELEVANCE
    except OverflowError:  # an int too large for a float
        valid = False
    if not valid:
        raise ValueError("relevance must be finite and within 0..100")
    await _locked(session, listing_id)
    await repo.set_relevance(session, listing_id, float(relevance))


# ---- takedown


def _block_target(kind: str, value: str) -> tuple[str | None, str]:
    """``(source, normalised value)`` of a takedown; ``ValueError`` for bad input.
    ``listing`` takes ``SOURCE:SOURCE_JOB_ID``."""
    if kind not in BLOCK_KINDS:
        raise ValueError(f"kind must be one of {', '.join(BLOCK_KINDS)}")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must not be empty")
    if kind == "listing":
        source, _, job_id = value.partition(":")
        source, job_id = source.strip(), job_id.strip()
        if not re.fullmatch(SOURCE_ID_PATTERN, source) or not job_id:
            raise ValueError("a listing takedown looks like SOURCE:SOURCE_JOB_ID")
        if len(job_id) > MAX_BLOCK_VALUE_CHARS:
            raise ValueError(f"the listing is longer than {MAX_BLOCK_VALUE_CHARS} characters")
        return source, job_id
    if kind == "url":
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("a url takedown needs an absolute http(s) URL")
        normalised = canonical_url(value)
    else:
        normalised = normalize_company(value)
    if len(normalised) > MAX_BLOCK_VALUE_CHARS:
        raise ValueError(f"the {kind} is longer than {MAX_BLOCK_VALUE_CHARS} characters")
    return None, normalised


def _same_url(stored: str, canonical: str) -> bool:
    """Whether a stored URL has this canonical form; one that cannot be parsed never does."""
    try:
        return canonical_url(stored) == canonical
    except ValueError:
        return False


async def _blocked_listing_ids(
    session: AsyncSession, kind: str, source: str | None, value: str
) -> list[uuid.UUID]:
    if kind == "listing":
        assert source is not None
        return await repo.listing_ids_by_source_job(session, source, value)
    if kind == "url":
        return [
            i
            for i, url in await repo.listing_ids_and_urls(session)
            if _same_url(url, value)
        ]
    return [
        i
        for i, company in await repo.listing_ids_and_companies(session)
        if normalize_company(company) == value
    ]


class TakedownBusyError(ValueError):
    """A takedown could not get its lock in time: an ingest is in flight. Nothing was
    changed; the session must be rolled back. Retry."""


async def _lock_for_takedown(session: AsyncSession) -> None:
    try:
        await repo.lock_blocks(session, exclusive=True, timeout_ms=BLOCK_LOCK_TIMEOUT_MS)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":  # lock_not_available
            raise TakedownBusyError("an ingest is in flight; nothing changed, retry") from None
        raise


async def block(
    session: AsyncSession, kind: str, value: str, *, actor_id: uuid.UUID | None = None
) -> int:
    """Take down a Listing (``SOURCE:ID``), a URL or a company: records the block (so a
    matching draft is never ingested again), deletes every matching Listing now (pinned ones
    too: a takedown wins) and writes an ``admin_audit`` row (``listings.block``; ``actor_id``
    None is the CLI) and a counts-only ``listings.blocked`` event. Blocking twice is safe.
    Returns the number of Listings deleted. Does not commit."""
    source, normalised = _block_target(kind, value)
    await _lock_for_takedown(session)
    created = await repo.insert_block(session, kind, source, normalised)
    removed = await repo.delete_listings(
        session, await _blocked_listing_ids(session, kind, source, normalised)
    )
    await record_audit(session, BLOCK_ACTION, actor_id=actor_id, target_account_id=None)
    await record_event(session, BLOCKED_EVENT, counts={"removed": removed, "created": int(created)})
    return removed


async def unblock(
    session: AsyncSession, kind: str, value: str, *, actor_id: uuid.UUID | None = None
) -> bool:
    """Lift a takedown (later ingests succeed again; deleted Listings are not restored).
    Writes an ``admin_audit`` row (``listings.unblock``) when there was one; an unknown
    block is a no-op returning False. Does not commit."""
    source, normalised = _block_target(kind, value)
    await _lock_for_takedown(session)
    if not await repo.delete_block(session, kind, source, normalised):
        return False
    await record_audit(session, UNBLOCK_ACTION, actor_id=actor_id, target_account_id=None)
    await record_event(session, UNBLOCKED_EVENT, counts={"unblocked": 1})
    return True


# --------------------------------------------------------------------------- scheduling


def collect_key(owner_id: uuid.UUID, day: date) -> str:
    """The ``listings.collect`` job key for ``owner_id`` on the Student's local ``day``."""
    return f"{COLLECT}:student:{owner_id}:{day.isoformat()}"


async def collect_for_owner(owner_id: uuid.UUID) -> RunOutcome | None:
    """The ``listings.collect`` job: one run scoped to the owner's Preferences, at most one
    per local day (``run_key`` ``collect:<owner>:<local_date>``, the day in the Student's
    time zone). Without role titles it records ``listings.collect_skipped``
    (``no_preferences``) and returns None."""
    async with get_sessionmaker()() as session:
        prefs = await profile_service.get_preferences(session, owner_id)
        if prefs is None or not prefs.role_titles:
            await record_event(
                session, SKIPPED_EVENT, owner_id=owner_id, counts={"no_preferences": 1}
            )
            await session.commit()
            return None
        params = {
            "role_titles": list(prefs.role_titles),
            "locations": list(prefs.locations),
            "job_types": list(prefs.job_types),
        }
        day = local_day(prefs.timezone)
    return await start_provider_run(
        owner_id, PURPOSE_JOBS, params, run_key=f"collect:{owner_id}:{day.isoformat()}"
    )


async def on_provider_key_changed(session: AsyncSession, event: ProviderKeyChanged) -> None:
    """``ProviderKeyChanged`` subscriber: a saved key creates the owner's daily
    ``listings.collect`` schedule, or resumes a paused one, with the next run at
    ``COLLECT_LOCAL_HOUR``:00 in the Student's time zone (an active schedule keeps its next
    run), and enqueues today's first collection, in the saving transaction. Resuming is
    safe: every start re-checks consent and ``provider_status``. A deleted key is handled
    by ``accounts`` (schedules paused)."""
    if event.removed:
        return
    settings = get_settings()
    prefs = await profile_service.get_preferences(session, event.owner_id)
    tz = prefs.timezone if prefs is not None else None
    await upsert_owner_schedule(
        session,
        COLLECT,
        event.owner_id,
        interval=timedelta(hours=settings.collect_interval_hours),
        next_run_at=next_local_time(tz, settings.collect_local_hour),
    )
    await enqueue(
        session, COLLECT, collect_key(event.owner_id, local_day(tz)), owner_id=event.owner_id
    )
