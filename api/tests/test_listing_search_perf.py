"""NFR-2: keyword search stays under 500 ms at about 30,000 Listings (Story 2.6).

Seeds about 30,000 active Listings with one ``generate_series`` insert (realistic titles,
companies, skills and description lexemes; about one in ten private to some Student; a few
expired) and times :func:`app.modules.listings.service.search` itself, so the budget covers
the query and the page of results, not HTTP. Each query is timed after a warm-up and the best
of a few runs must be under the budget.
"""

import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.listings import service
from tests.conftest import AccountFactory
from tests.listing_fixtures import ListingDb, listing_db  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("clean_jobs")

POOL_SIZE = 30_000
BUDGET_SECONDS = 0.5
RUNS = 3
# A word that appears in only a handful of Listings.
RARE_WORD = "quetzalcoatlus"

SEED = """
INSERT INTO app.listings (
    id, source, source_job_id, url, title, company, location, job_type, skills, vocab_version,
    search_doc, first_seen_at, last_seen_at, visibility_owner_id, expired_at
)
SELECT
    gen_random_uuid(), 'linkedin', 'perf-' || g, 'https://example.test/jobs/' || g,
    t.title, t.company, 'Bengaluru', 'internship', t.skills, 'perf',
    setweight(to_tsvector('english', t.title), 'A')
      || setweight(to_tsvector('english', t.company), 'B')
      || setweight(to_tsvector('english', array_to_string(t.skills, ' ')), 'C')
      || setweight(to_tsvector('english', t.body), 'D'),
    now() - (g || ' seconds')::interval, now(),
    CASE WHEN g % 10 = 0 THEN ({OWNERS}::uuid[])[1 + (g / 10) % 3] END,
    CASE WHEN g % 97 = 0 THEN now() END
FROM (
    SELECT g,
        (ARRAY['Backend','Frontend','Full Stack','Data','Machine Learning','DevOps','Mobile',
               'Security','Cloud','Platform','QA','Product'])[1 + g % 12]
        || ' ' ||
        (ARRAY['Intern','Engineer','Analyst','Developer','Internship','Associate',
               'Software Engineer','Researcher'])[1 + (g / 12) % 8]
        || CASE WHEN g % 5003 = 0 THEN ' {RARE}' ELSE '' END AS title,
        (ARRAY['Acme Corp','Globex','Initech','Umbrella Labs','Hooli','Stark Industries',
               'Wayne Enterprises','Soylent','Vandelay','Pied Piper','Aperture Science',
               'Cyberdyne','Tyrell','Massive Dynamic','Wonka','Oscorp','Gringotts','Dunder Mifflin',
               'Prestige Worldwide','Bluth Company'])[1 + (g / 7) % 20]
        || ' ' || (g % 997) AS company,
        string_to_array((ARRAY['python postgresql','java spring','react typescript',
               'docker kubernetes','sql excel','go redis','pytorch python'])[1 + (g / 3) % 7],
               ' ') AS skills,
        'We are looking for a motivated candidate to join our team and help build reliable '
        || 'products. You will collaborate with engineers, write clean code, review designs, '
        || 'and learn from mentors across ' || (ARRAY['payments','search','analytics','growth',
        'infrastructure','security'])[1 + (g / 11) % 6] || ' projects every week. '
        -- Several KB of varied terms (250 per row out of 20,000), like a real description's
        -- lexemes, so documents are as large as in the pool.
        || (SELECT string_agg('lex' || (abs(hashtext(g || ':' || i)) % 20000), ' ')
            FROM generate_series(1, 250) AS i) AS body
    FROM generate_series(1, {N}) AS g
) t
"""

QUERIES = {
    "two words": ("backend intern", True),
    "typo": ("bakend intrn", True),
    "common word": ("intern", True),
    "rare word": (RARE_WORD, True),
    "no match": ("zzzzqqqq", False),
}
# The deepest page search allows: 50 results from offset 200.
DEEP_PAGE = ("intern", 50, 200)


@pytest.fixture
def pool(listing_db: ListingDb, accounts: AccountFactory) -> Iterator[list[uuid.UUID]]:
    owners = [accounts.create() for _ in range(3)]
    array = "ARRAY[" + ", ".join(f"'{owner}'" for owner in owners) + "]"
    sql = SEED.replace("{OWNERS}", array).replace("{RARE}", RARE_WORD)
    listing_db.execute(sql.replace("{N}", str(POOL_SIZE)))
    listing_db.execute("ANALYZE app.listings")
    yield owners


async def _best_of(
    engine: AsyncEngine, actor: uuid.UUID, query: str, limit: int = 20, offset: int = 0
):
    best = float("inf")
    count = 0
    async with AsyncSession(engine) as session:
        for _ in range(RUNS):
            start = time.perf_counter()
            page = await service.search(session, actor, query=query, limit=limit, offset=offset)
            best = min(best, time.perf_counter() - start)
            count = len(page.items)
            await session.rollback()
    return best, count


async def test_search_under_500_ms_at_30k_listings(
    engine: AsyncEngine, pool: list[uuid.UUID], listing_db: ListingDb
) -> None:
    assert listing_db.count() == POOL_SIZE
    actor = pool[0]
    cases = {name: (q, 20, 0, expects) for name, (q, expects) in QUERIES.items()}
    cases["deep page"] = (*DEEP_PAGE, True)
    async with AsyncSession(engine) as session:  # warm-up: connections, plans, caches
        for query, limit, offset, _ in cases.values():
            await service.search(session, actor, query=query, limit=limit, offset=offset)
        await session.rollback()
    timings: dict[str, float] = {}
    for name, (query, limit, offset, expects_results) in cases.items():
        best, count = await _best_of(engine, actor, query, limit, offset)
        timings[name] = best
        assert (count > 0) == expects_results, f"{name}: {count} results"
    print({name: f"{secs * 1000:.0f} ms" for name, secs in timings.items()})
    for name, secs in timings.items():
        assert secs < BUDGET_SECONDS, f"{name} took {secs * 1000:.0f} ms (budget 500 ms)"
