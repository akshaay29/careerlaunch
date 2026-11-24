"""listings.repo: SQL for the ``sources``, ``listings``, ``provider_runs`` and
``listing_blocks`` tables only (AD-2)."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Float,
    Text,
    bindparam,
    case,
    false,
    func,
    literal_column,
    or_,
    select,
    text,
    true,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import quoted_name
from sqlalchemy.sql.functions import Function

from app.modules.listings.models import Listing, ProviderRun, Source


async def get_source(session: AsyncSession, source_id: str) -> Source | None:
    return await session.get(Source, source_id, populate_existing=True)


async def upsert_source(session: AsyncSession, source_id: str, label: str) -> None:
    """Create the Source (enabled) or refresh its label; ``enabled`` is never changed."""
    stmt = (
        insert(Source)
        .values(id=source_id, label=label)
        .on_conflict_do_update(index_elements=[Source.id], set_={"label": label})
    )
    await session.execute(stmt)


# One statement: insert, or refresh on (source, source_job_id). The description reaches
# Postgres only as the ``:description`` bind parameter of ``to_tsvector`` (AD-7): its
# lexemes are stored in ``search_doc``, the text never is.
#
# On conflict, only an AUTHORISED writer refreshes the content: a contributing collector, or
# the collector the row is private to. Anyone else (a non-contributing Student re-collecting
# a pool row or another Student's private row) only advances ``last_seen_at``, so their scrape
# is never shared without consent. An authorised refresh keeps known values when the new
# scrape is thinner (NULL / empty fields, no skills, no description), and a Listing seen again
# whose deadline is unknown or not past is no longer expired.
#
# Visibility only widens: a contributing collector makes a private row public (NULL) and
# becomes its ``collected_by``; a non-contributing collector never changes a row's
# visibility or collector.
_AUTH = (
    "COALESCE(CAST(:contributes AS boolean) "
    "OR l.visibility_owner_id = CAST(:collected_by AS uuid), false)"
)
_DEADLINE = "COALESCE(EXCLUDED.deadline, l.deadline)"
_UPSERT = text(
    f"""
    WITH seen AS (SELECT clock_timestamp() AS ts)
    INSERT INTO listings AS l (
        id, source, source_job_id, url, title, company, location, job_type,
        posted_at, deadline, skills, vocab_version, search_doc,
        first_seen_at, last_seen_at, collected_by, visibility_owner_id
    )
    VALUES (
        :id, :source, :source_job_id, :url, :title, :company, :location, :job_type,
        :posted_at, :deadline, CAST(:skills AS text[]), :vocab_version,
        setweight(to_tsvector('english'::regconfig, CAST(:title AS text)), 'A')
          || setweight(to_tsvector('english'::regconfig, CAST(:company AS text)), 'B')
          || setweight(to_tsvector('english'::regconfig, CAST(:skill_labels AS text)), 'C')
          || setweight(to_tsvector('english'::regconfig, CAST(:description AS text)), 'D'),
        -- One clock reading for both columns, so a new row has first_seen_at = last_seen_at
        -- exactly (two clock_timestamp() calls could differ by a microsecond).
        (SELECT ts FROM seen), (SELECT ts FROM seen),
        CAST(:collected_by AS uuid),
        CASE WHEN CAST(:contributes AS boolean) THEN NULL ELSE CAST(:collected_by AS uuid) END
    )
    ON CONFLICT (source, source_job_id) DO UPDATE SET
        url = CASE WHEN {_AUTH} THEN EXCLUDED.url ELSE l.url END,
        title = CASE WHEN {_AUTH} THEN EXCLUDED.title ELSE l.title END,
        company = CASE WHEN {_AUTH} THEN COALESCE(NULLIF(EXCLUDED.company, ''), l.company)
                       ELSE l.company END,
        location = CASE WHEN {_AUTH} THEN COALESCE(NULLIF(EXCLUDED.location, ''), l.location)
                        ELSE l.location END,
        job_type = CASE WHEN {_AUTH} THEN COALESCE(EXCLUDED.job_type, l.job_type)
                        ELSE l.job_type END,
        posted_at = CASE WHEN {_AUTH} THEN COALESCE(EXCLUDED.posted_at, l.posted_at)
                         ELSE l.posted_at END,
        deadline = CASE WHEN {_AUTH} THEN {_DEADLINE} ELSE l.deadline END,
        skills = CASE WHEN {_AUTH} AND cardinality(EXCLUDED.skills) > 0
                      THEN EXCLUDED.skills ELSE l.skills END,
        vocab_version = CASE WHEN {_AUTH} AND cardinality(EXCLUDED.skills) > 0
                             THEN EXCLUDED.vocab_version ELSE l.vocab_version END,
        search_doc = CASE WHEN {_AUTH} THEN
                ts_filter(EXCLUDED.search_doc, '{{a}}')
                || CASE WHEN EXCLUDED.company = '' THEN ts_filter(l.search_doc, '{{b}}')
                        ELSE ts_filter(EXCLUDED.search_doc, '{{b}}') END
                || CASE WHEN cardinality(EXCLUDED.skills) = 0 THEN ts_filter(l.search_doc, '{{c}}')
                        ELSE ts_filter(EXCLUDED.search_doc, '{{c}}') END
                || CASE WHEN CAST(:description AS text) = ''
                        THEN ts_filter(l.search_doc, '{{d}}')
                        ELSE ts_filter(EXCLUDED.search_doc, '{{d}}') END
            ELSE l.search_doc END,
        expired_at = CASE WHEN {_AUTH} AND ({_DEADLINE} IS NULL OR {_DEADLINE} >= CURRENT_DATE)
                          THEN NULL ELSE l.expired_at END,
        last_seen_at = EXCLUDED.last_seen_at,
        collected_by = CASE
            WHEN CAST(:contributes AS boolean) AND l.visibility_owner_id IS NOT NULL
            THEN EXCLUDED.collected_by
            ELSE l.collected_by
        END,
        visibility_owner_id = CASE
            WHEN CAST(:contributes AS boolean) THEN NULL
            ELSE l.visibility_owner_id
        END
    RETURNING id, (xmax = 0) AS created, visibility_owner_id
    """
)


@dataclass(frozen=True)
class UpsertResult:
    listing_id: uuid.UUID
    created: bool
    visibility_owner_id: uuid.UUID | None


async def upsert_listing(
    session: AsyncSession,
    *,
    source: str,
    source_job_id: str,
    url: str,
    title: str,
    company: str,
    location: str,
    job_type: str | None,
    posted_at: datetime | None,
    deadline: date | None,
    skills: list[str],
    skill_labels: str,
    vocab_version: str,
    description: str,
    collected_by: uuid.UUID | None,
    contributes_to_pool: bool,
) -> UpsertResult:
    """Insert or refresh one Listing; returns its id, whether it was inserted, and its
    visibility after the write."""
    row = (
        await session.execute(
            _UPSERT,
            {
                "id": uuid.uuid7(),
                "source": source,
                "source_job_id": source_job_id,
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "job_type": job_type,
                "posted_at": posted_at,
                "deadline": deadline,
                "skills": skills,
                "skill_labels": skill_labels,
                "vocab_version": vocab_version,
                "description": description,
                "collected_by": collected_by,
                "contributes": contributes_to_pool,
            },
        )
    ).one()
    return UpsertResult(row.id, bool(row.created), row.visibility_owner_id)


def _visible_to(actor_id: uuid.UUID):  # noqa: ANN202 (SQL expression)
    return or_(Listing.visibility_owner_id.is_(None), Listing.visibility_owner_id == actor_id)


async def browse_page(
    session: AsyncSession,
    actor_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[tuple[Listing, str]]:
    """Listings visible to ``actor_id`` and not expired, newest first by
    ``(first_seen_at, id)``; ``after`` is the exclusive keyset cursor. Each row comes with its
    Source label."""
    stmt = (
        select(Listing, Source.label)
        .join(Source, Source.id == Listing.source)
        .where(_visible_to(actor_id), Listing.expired_at.is_(None))
    )
    if after is not None:
        stmt = stmt.where(tuple_(Listing.first_seen_at, Listing.id) < tuple_(*after))
    stmt = (
        stmt.order_by(Listing.first_seen_at.desc(), Listing.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return [(listing, label) for listing, label in (await session.execute(stmt)).all()]


_trgm_schema_cache: str | None = None


async def _trgm_schema(session: AsyncSession) -> str:
    """The schema ``pg_trgm`` lives in (``extensions`` on Supabase, ``public`` on plain
    Postgres, or wherever it was already enabled). The session's ``search_path`` is ``app``,
    so its functions and operators are always schema-qualified."""
    global _trgm_schema_cache
    if _trgm_schema_cache is None:
        name = (
            await session.execute(
                text(
                    "SELECT n.nspname FROM pg_extension e "
                    "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
                )
            )
        ).scalar_one_or_none()
        if name is None:
            raise RuntimeError("the pg_trgm extension is not enabled (run the migrations)")
        _trgm_schema_cache = str(name)
    return _trgm_schema_cache


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def trigram_expressions(schema: str, phrase: Any, title_company: Any) -> tuple[Any, Any]:
    """``(similar, similarity)`` for ``phrase`` against ``title_company``: the ``<%`` test and
    ``word_similarity()``, both qualified with ``schema`` (always quoted)."""
    similar = phrase.op(f"OPERATOR({_quote_ident(schema)}.<%)")(title_company)
    # `quote=False`: the name is already quoted, so SQLAlchemy emits it verbatim.
    similarity = Function(
        quoted_name(f"{_quote_ident(schema)}.word_similarity", False),
        phrase,
        title_company,
        type_=Float,
    )
    return similar, similarity


async def tsquery_has_lexemes(session: AsyncSession, tsquery: str) -> bool:
    """Whether ``to_tsquery('english', tsquery)`` keeps any term (a query of only stop words
    such as "the" or "to be" is empty and would match nothing by full text)."""
    nodes = (
        await session.execute(text("SELECT numnode(to_tsquery('english', :q))"), {"q": tsquery})
    ).scalar_one()
    return bool(nodes)


async def search_page(
    session: AsyncSession,
    actor_id: uuid.UUID,
    *,
    tsquery: str,
    phrase: str,
    threshold: float,
    limit: int,
    offset: int,
) -> list[tuple[Listing, str]]:
    """Listings visible to ``actor_id`` and not expired that match, best first.

    ``tsquery`` is a prefix tsquery string built only from sanitised tokens and ``phrase`` the
    same tokens joined by spaces; both reach Postgres as bind parameters. A Listing matches
    when its ``search_doc`` matches ``tsquery``, or the trigram word similarity of ``phrase``
    to ``lower(title || ' ' || company)`` reaches ``threshold``. Full-text matches rank above
    similarity-only ones, then by ``ts_rank_cd`` plus similarity, then newest first (so pages
    are stable)."""
    schema = await _trgm_schema(session)
    # The `<%` operator (which the trigram index serves) reads its threshold from this setting.
    # With Postgres' default `random_page_cost` (4, tuned for spinning disks) the planner prefers
    # a sequential scan that evaluates the trigram test on every row (over 100 ms at 30,000
    # Listings); the pool's database is on SSD, so plan with the realistic cost and let both GIN
    # indexes answer. LOCAL: both end with the transaction.
    await session.execute(
        text(
            "SELECT set_config('pg_trgm.word_similarity_threshold', :t, true), "
            "set_config('random_page_cost', '1.1', true)"
        ),
        {"t": repr(float(threshold))},
    )
    tsq = func.to_tsquery(
        literal_column("'english'::regconfig"), bindparam("tsq", tsquery, type_=Text)
    )
    phrase_param = bindparam("phrase", phrase, type_=Text)
    title_company = literal_column("listings.title_company", type_=Text)
    full_text = Listing.search_doc.op("@@")(tsq)
    similar, similarity = trigram_expressions(schema, phrase_param, title_company)
    # Candidates come from the two GIN indexes separately (full text, trigram), so the large
    # out-of-line ``search_doc`` values are read only for matching rows; one OR over the table
    # makes the planner scan every row and detoast every ``search_doc``.
    matched = union_all(
        select(Listing.id.label("id"), true().label("ft")).where(full_text),
        select(Listing.id.label("id"), false().label("ft")).where(similar),
    ).subquery("matched")
    cand = (
        select(matched.c.id, func.bool_or(matched.c.ft).label("ft"))
        .group_by(matched.c.id)
        .cte("cand")
    )
    rank = case((cand.c.ft, func.ts_rank_cd(Listing.search_doc, tsq)), else_=0.0)
    stmt = (
        select(Listing, Source.label)
        .join(cand, cand.c.id == Listing.id)
        .join(Source, Source.id == Listing.source)
        .where(_visible_to(actor_id), Listing.expired_at.is_(None))
        .order_by(
            cand.c.ft.desc(),
            (rank + similarity).desc(),
            Listing.first_seen_at.desc(),
            Listing.id.desc(),
        )
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [(listing, label) for listing, label in (await session.execute(stmt)).all()]


async def get_visible(
    session: AsyncSession, actor_id: uuid.UUID, listing_id: uuid.UUID
) -> tuple[Listing, str] | None:
    """One Listing visible to ``actor_id`` and not expired, with its Source label."""
    stmt = (
        select(Listing, Source.label)
        .join(Source, Source.id == Listing.source)
        .where(Listing.id == listing_id, _visible_to(actor_id), Listing.expired_at.is_(None))
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(stmt)).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def get_visible_scoring_input(
    session: AsyncSession, actor_id: uuid.UUID, listing_id: uuid.UUID
) -> tuple[str, list[str], str, list[str]] | None:
    """``(title, skills, vocab_version, search_doc lexemes)`` of one Listing visible to
    ``actor_id`` and not expired; None otherwise."""
    stmt = select(
        Listing.title,
        Listing.skills,
        Listing.vocab_version,
        func.tsvector_to_array(Listing.search_doc),
    ).where(Listing.id == listing_id, _visible_to(actor_id), Listing.expired_at.is_(None))
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    return row[0], list(row[1] or []), row[2], list(row[3] or [])


MANUAL_SOURCE = "manual"


async def candidate_scoring_inputs(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    role_tsquery: str | None,
    role_phrases: list[str] | None,
    locations: list[str],
    job_types: list[str],
    threshold: float,
    cap: int,
) -> list[tuple[uuid.UUID, str, list[str], str, list[str]]]:
    """``(id, title, skills, vocab_version, search_doc lexemes)`` of a Student's candidate
    set (Story 3.2), in one query. Only Listings visible to ``owner_id`` and not expired.

    The ``cap`` newest (``first_seen_at``) Listings that pass every non-empty filter:

    * role: the title matches ``role_tsquery`` (a prefix tsquery restricted to the title's
      weight, built from sanitised tokens only), or ``word_similarity`` of one of
      ``role_phrases`` to the lower-cased title reaches ``threshold``. ``role_phrases`` None:
      no role filter; empty (role titles with no word in them): nothing matches.
    * location: the location contains one of ``locations``, ignoring case, or is unknown
      (empty);
    * job type: ``job_type`` is one of ``job_types``, or NULL;

    plus every Listing private to the Student (``source='manual'``), never capped."""
    visible = (_visible_to(owner_id), Listing.expired_at.is_(None))
    # NULL-safe: a pool row (visibility_owner_id NULL) is never "manual for this owner".
    manual = (Listing.source == MANUAL_SOURCE) & func.coalesce(
        Listing.visibility_owner_id == owner_id, false()
    )

    filters: list[Any] = [*visible, ~manual]
    if role_phrases is not None and not role_phrases:
        filters.append(false())
    elif role_phrases:
        schema = await _trgm_schema(session)
        title = func.lower(Listing.title)
        role_matches: list[Any] = [
            Function(
                quoted_name(f"{_quote_ident(schema)}.word_similarity", False),
                bindparam(f"role_{i}", phrase, type_=Text),
                title,
                type_=Float,
            )
            >= bindparam("threshold", float(threshold), type_=Float)
            for i, phrase in enumerate(role_phrases)
        ]
        if role_tsquery:
            tsq = func.to_tsquery(
                literal_column("'english'::regconfig"),
                bindparam("role_tsq", role_tsquery, type_=Text),
            )
            role_matches.append(Listing.search_doc.op("@@")(tsq))
        filters.append(or_(*role_matches))
    if locations:
        place = func.lower(func.coalesce(Listing.location, ""))
        filters.append(
            or_(
                place == "",
                *(
                    func.strpos(place, func.lower(bindparam(f"loc_{i}", loc, type_=Text))) > 0
                    for i, loc in enumerate(locations)
                )
            )
        )
    if job_types:
        filters.append(
            or_(Listing.job_type.is_(None), Listing.job_type.in_(list(job_types)))
        )

    pool = (
        select(Listing.id.label("id"))
        .where(*filters)
        .order_by(Listing.first_seen_at.desc(), Listing.id.desc())
        .limit(cap)
        .subquery("pool")
    )
    own = select(Listing.id.label("id")).where(*visible, manual)
    ids = union_all(select(pool.c.id), own).subquery("cand")
    stmt = (
        select(
            Listing.id,
            Listing.title,
            Listing.skills,
            Listing.vocab_version,
            func.tsvector_to_array(Listing.search_doc),
        )
        .join(ids, ids.c.id == Listing.id)
        .order_by(Listing.id)
    )
    return [
        (row[0], row[1] or "", list(row[2] or []), row[3], list(row[4] or []))
        for row in (await session.execute(stmt)).all()
    ]


async def lock_existing(
    session: AsyncSession, listing_ids: list[uuid.UUID], *, for_update: bool
) -> list[uuid.UUID]:
    """Lock the rows of ``listing_ids`` that still exist, in id order, until the transaction
    ends; returns their ids. ``for_update`` takes ``FOR NO KEY UPDATE`` (row writers such as
    relevance), else ``FOR KEY SHARE`` (keeps them from being deleted while rows referencing
    them are written)."""
    if not listing_ids:
        return []
    stmt = select(Listing.id).where(Listing.id.in_(listing_ids)).order_by(Listing.id)
    stmt = stmt.with_for_update(key_share=True) if for_update else stmt.with_for_update(
        read=True, key_share=True
    )
    return list((await session.execute(stmt)).scalars())


async def has_pool_listing(session: AsyncSession, listing_ids: list[uuid.UUID]) -> bool:
    """Whether one of ``listing_ids`` is in the shared pool (not private to a Student)."""
    if not listing_ids:
        return False
    stmt = select(
        select(Listing.id)
        .where(Listing.id.in_(listing_ids), Listing.visibility_owner_id.is_(None))
        .exists()
    )
    return bool((await session.execute(stmt)).scalar())


# --------------------------------------------------------------------------- provider runs


async def lock_run_key(session: AsyncSession, run_key: str) -> None:
    """Serialise every writer of ``run_key`` until the transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": run_key}
    )


async def get_run_by_key(session: AsyncSession, run_key: str) -> ProviderRun | None:
    stmt = (
        select(ProviderRun)
        .where(ProviderRun.run_key == run_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_run(
    session: AsyncSession, run_id: uuid.UUID, *, for_update: bool = False
) -> ProviderRun | None:
    stmt = select(ProviderRun).where(ProviderRun.id == run_id)
    if for_update:
        stmt = stmt.with_for_update()
    stmt = stmt.execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def insert_run(session: AsyncSession, **values: Any) -> ProviderRun:
    run = ProviderRun(id=uuid.uuid7(), **values)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def update_run(session: AsyncSession, run_id: uuid.UUID, **values: Any) -> None:
    await session.execute(update(ProviderRun).where(ProviderRun.id == run_id).values(**values))


async def stale_runs(
    session: AsyncSession, status: str, before: datetime, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID | None, str | None]]:
    """``(id, owner_id, apify_run_id)`` of rows in ``status`` created (``starting``) or
    started (``running``) before ``before``, oldest first."""
    column = ProviderRun.started_at if status == "running" else ProviderRun.created_at
    stmt = (
        select(ProviderRun.id, ProviderRun.owner_id, ProviderRun.apify_run_id)
        .where(ProviderRun.status == status, column < before)
        .order_by(column)
        .limit(limit)
    )
    return [(r.id, r.owner_id, r.apify_run_id) for r in (await session.execute(stmt)).all()]


async def lock_spend(session: AsyncSession, scope: str) -> None:
    """Serialise the cap check and the insert of runs in ``scope`` (``owner:<id>`` or
    ``platform``) until the transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"spend:{scope}"}
    )


@dataclass(frozen=True)
class MonthSpend:
    """Month-to-date spend in USD: ``spent`` is the cost recorded so far (finished runs and
    the cost so far of runs in flight); ``committed`` adds what runs in flight may still
    charge (their ``max_charge_usd`` minus their cost so far)."""

    spent: Decimal
    committed: Decimal


_COST = func.coalesce(ProviderRun.cost_usd, 0)
_IN_FLIGHT = ("starting", "running")
# A failed run that reached Apify but never recorded a cost (start_unknown, timeout, reaped)
# may have spent up to its cap: count it at the cap.
_SPENT = case(
    (
        (ProviderRun.status == "failed")
        & ProviderRun.cost_usd.is_(None)
        & ProviderRun.apify_run_id.is_not(None),
        ProviderRun.max_charge_usd,
    ),
    else_=_COST,
)


async def month_spend(
    session: AsyncSession, since: datetime, owner_id: uuid.UUID | None = None
) -> MonthSpend:
    """Spend of runs created since ``since``, plus every run still in flight whenever it was
    created: one owner's, or (``owner_id`` None) every Student's (System seed runs excluded).
    Refused rows never spent anything; a failed run with an Apify run id and no recorded cost
    counts at its ``max_charge_usd``."""
    remaining = case(
        (
            ProviderRun.status.in_(_IN_FLIGHT),
            func.greatest(ProviderRun.max_charge_usd - _COST, 0),
        ),
        else_=0,
    )
    stmt = select(
        func.coalesce(func.sum(_SPENT), 0), func.coalesce(func.sum(remaining), 0)
    ).where(
        or_(
            (ProviderRun.created_at >= since) & (ProviderRun.status != "refused"),
            ProviderRun.status.in_(_IN_FLIGHT),
        )
    )
    if owner_id is None:
        stmt = stmt.where(ProviderRun.owner_id.is_not(None))
    else:
        stmt = stmt.where(ProviderRun.owner_id == owner_id)
    row = (await session.execute(stmt)).one()
    spent, rest = Decimal(str(row[0])), Decimal(str(row[1]))
    return MonthSpend(spent, spent + rest)


async def latest_runs(
    session: AsyncSession, owner_id: uuid.UUID, limit: int
) -> list[ProviderRun]:
    """The owner's newest runs first."""
    stmt = (
        select(ProviderRun)
        .where(ProviderRun.owner_id == owner_id)
        .order_by(ProviderRun.created_at.desc(), ProviderRun.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- maintenance (2.5)
#
# Each batch statement locks its candidate rows with ``FOR UPDATE SKIP LOCKED`` (a row another
# transaction holds, e.g. a ``pin``, is skipped, never waited for) and the DELETEs re-check
# ``pin_count = 0``. Callers loop until a batch changes nothing.

_EXPIRE_BATCH = text(
    """
    WITH threshold AS (
        -- The Nth most recent succeeded linkedin run: a Listing last seen before it has
        -- missed at least N runs. NULL while fewer than N runs have ever succeeded.
        SELECT (SELECT r.finished_at FROM provider_runs r
                WHERE r.status = 'succeeded' AND r.purpose = 'jobs'
                  AND r.finished_at IS NOT NULL
                ORDER BY r.finished_at DESC OFFSET :runs_before LIMIT 1) AS cutoff
    ), due AS MATERIALIZED (
        SELECT l.id FROM listings l, threshold
        WHERE l.expired_at IS NULL
          AND l.pin_count = 0
          AND (l.deadline < CURRENT_DATE
               OR (l.source = 'linkedin'
                   AND l.last_seen_at < now() - make_interval(days => :days)
                   AND threshold.cutoff IS NOT NULL
                   -- The Nth most recent run must itself be recent: after a burst of runs
                   -- and then silence nothing expires (an idle pool never empties itself).
                   AND threshold.cutoff >= now() - make_interval(days => :days)
                   AND l.last_seen_at < threshold.cutoff))
        ORDER BY l.id
        LIMIT :batch
        FOR UPDATE OF l SKIP LOCKED
    )
    UPDATE listings SET expired_at = now()
    FROM due WHERE listings.id = due.id
    RETURNING (listings.deadline < CURRENT_DATE) AS by_deadline
    """
)

# The candidates are a MATERIALIZED CTE: ``DELETE ... WHERE id IN (SELECT ... LIMIT n FOR
# UPDATE SKIP LOCKED)`` may re-run its subquery per row and delete more than ``n``.
_PURGE_BATCH = text(
    """
    WITH due AS MATERIALIZED (
        SELECT id FROM listings
        WHERE expired_at < now() - make_interval(days => :grace) AND pin_count = 0
        ORDER BY expired_at, id
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM listings USING due
    WHERE listings.id = due.id AND listings.pin_count = 0
    RETURNING listings.id
    """
)

_EVICT_BATCH = text(
    """
    WITH due AS MATERIALIZED (
        SELECT id FROM listings
        WHERE expired_at IS NULL AND pin_count = 0
        ORDER BY relevance, last_seen_at, id
        LIMIT :batch
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM listings USING due
    WHERE listings.id = due.id AND listings.pin_count = 0
    RETURNING listings.id
    """
)


async def expire_batch(
    session: AsyncSession, *, unseen_days: int, unseen_runs: int, batch: int
) -> tuple[int, int]:
    """Mark up to ``batch`` due Listings expired; returns ``(by_deadline, by_unseen)``."""
    rows = (
        await session.execute(
            _EXPIRE_BATCH,
            {"runs_before": unseen_runs - 1, "days": unseen_days, "batch": batch},
        )
    ).all()
    by_deadline = sum(1 for r in rows if r.by_deadline)
    return by_deadline, len(rows) - by_deadline


async def purge_batch(session: AsyncSession, *, grace_days: int, batch: int) -> int:
    """Delete up to ``batch`` unpinned Listings expired longer than ``grace_days`` ago."""
    result = await session.execute(_PURGE_BATCH, {"grace": grace_days, "batch": batch})
    return len(result.all())


async def count_active(session: AsyncSession) -> int:
    """Listings that are not expired."""
    stmt = select(func.count()).select_from(Listing).where(Listing.expired_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def evict_batch(session: AsyncSession, *, batch: int) -> int:
    """Delete up to ``batch`` active, unpinned Listings: lowest relevance, then oldest
    ``last_seen_at``."""
    result = await session.execute(_EVICT_BATCH, {"batch": batch})
    return len(result.all())


async def database_size_bytes(session: AsyncSession) -> int:
    row = await session.execute(text("SELECT pg_database_size(current_database())"))
    return int(row.scalar_one())


async def lock_listing(session: AsyncSession, listing_id: uuid.UUID) -> Listing | None:
    """The Listing, row-locked until the transaction ends (None when it does not exist)."""
    # FOR NO KEY UPDATE: only non-key columns change, so this never waits on (or deadlocks
    # with) a ``FOR KEY SHARE`` held by a score writer (matching.recompute).
    stmt = (
        select(Listing)
        .where(Listing.id == listing_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_pin_count(session: AsyncSession, listing_id: uuid.UUID, count: int) -> None:
    """Set ``pin_count``; a pinned Listing is also no longer expired."""
    values: dict[str, Any] = {"pin_count": count}
    if count > 0:
        values["expired_at"] = None
    await session.execute(update(Listing).where(Listing.id == listing_id).values(**values))


async def set_relevance(session: AsyncSession, listing_id: uuid.UUID, relevance: float) -> None:
    await session.execute(
        update(Listing).where(Listing.id == listing_id).values(relevance=relevance)
    )


# --------------------------------------------------------------------------- takedown (2.5)


async def lock_blocks(
    session: AsyncSession, *, exclusive: bool, timeout_ms: int | None = None
) -> None:
    """Order takedowns against ingestion until the transaction ends: ``block``/``unblock``
    take it exclusively, every ingest shares it. An ingest that started before a block
    committed is therefore finished (and its row visible to the block's delete) first, and
    one that starts after sees the block."""
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    if timeout_ms is None:
        await session.execute(text(f"SELECT {fn}(hashtextextended('listing_blocks', 0))"))
        return
    # Wait at most ``timeout_ms`` (for this transaction), then put the old setting back.
    previous = (await session.execute(text("SELECT current_setting('lock_timeout')"))).scalar_one()
    await session.execute(
        text("SELECT set_config('lock_timeout', :v, true)"), {"v": f"{int(timeout_ms)}ms"}
    )
    await session.execute(text(f"SELECT {fn}(hashtextextended('listing_blocks', 0))"))
    await session.execute(text("SELECT set_config('lock_timeout', :v, true)"), {"v": previous})


async def is_blocked(
    session: AsyncSession, *, source: str, source_job_id: str, url: str, company: str
) -> bool:
    """Whether a block matches the draft: by Listing, by canonical URL or by company (all
    already normalised by the caller)."""
    row = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM listing_blocks
                WHERE (kind = 'listing' AND source = :source AND value = :job_id)
                   OR (kind = 'url' AND value = :url)
                   OR (kind = 'company' AND value = :company)
            )
            """
        ),
        {"source": source, "job_id": source_job_id, "url": url, "company": company},
    )
    return bool(row.scalar_one())


async def insert_block(session: AsyncSession, kind: str, source: str | None, value: str) -> bool:
    """Record a block; False when the same one already exists."""
    row = await session.execute(
        text(
            """
            INSERT INTO listing_blocks (id, kind, source, value)
            VALUES (:id, :kind, :source, :value)
            ON CONFLICT ON CONSTRAINT listing_blocks_kind_source_value_key DO NOTHING
            RETURNING id
            """
        ),
        {"id": uuid.uuid7(), "kind": kind, "source": source, "value": value},
    )
    return row.first() is not None


async def delete_block(session: AsyncSession, kind: str, source: str | None, value: str) -> bool:
    """Remove a block; False when there was none."""
    row = await session.execute(
        text(
            """
            DELETE FROM listing_blocks
            WHERE kind = :kind AND source IS NOT DISTINCT FROM :source AND value = :value
            RETURNING id
            """
        ),
        {"kind": kind, "source": source, "value": value},
    )
    return row.first() is not None


async def listing_ids_by_source_job(
    session: AsyncSession, source: str, source_job_id: str
) -> list[uuid.UUID]:
    stmt = select(Listing.id).where(
        Listing.source == source, Listing.source_job_id == source_job_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def listing_ids_and_urls(session: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    return [(r.id, r.url) for r in (await session.execute(select(Listing.id, Listing.url))).all()]


async def listing_ids_and_companies(session: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    stmt = select(Listing.id, Listing.company).where(Listing.company != "")
    return [(r.id, r.company) for r in (await session.execute(stmt)).all()]


async def delete_listings(session: AsyncSession, ids: list[uuid.UUID]) -> int:
    """Delete these Listings (pinned ones too: a takedown wins); returns how many existed."""
    if not ids:
        return 0
    result = await session.execute(
        text("DELETE FROM listings WHERE id = ANY(CAST(:ids AS uuid[])) RETURNING id"),
        {"ids": ids},
    )
    return len(result.all())
