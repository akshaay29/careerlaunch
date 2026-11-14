"""listings.repo: SQL for the ``sources`` and ``listings`` tables only (AD-2)."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.listings.models import Listing, Source


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
        clock_timestamp(), clock_timestamp(),
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
