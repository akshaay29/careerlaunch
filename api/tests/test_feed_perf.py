"""NFR-1: a feed page loads in under 2 s at about 30,000 Listings (Story 3.3).

Seeds about 30,000 active Listings with one ``generate_series`` insert (as the search perf
test; about one in ten private to some Student, a few expired) and 500 stored scores for one
Student, then times :func:`app.modules.matching.service.feed` itself: the first page, a deep
page (followed through the cursor), the first page with "Show below my threshold", and the
provisional path (a second Student with setup but no stored scores, scoring
``FEED_ON_REQUEST_CAP`` candidates). Each is timed after a warm-up; the best of 3 must be
under the budget: 2 s as written in CI (``CI=true``) or with ``PERF_STRICT=1``, otherwise
scaled by this machine's calibration (``tests/perf_budget.py``).
"""

import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.modules.matching import service
from tests.conftest import AccountFactory
from tests.match_fixtures import MatchDb
from tests.perf_budget import budget_for, calibrate
from tests.test_listing_search_perf import POOL_SIZE, RARE_WORD, SEED

pytestmark = pytest.mark.usefixtures("clean_jobs")

BUDGET_SECONDS = 2.0
RUNS = 3
SCORES = 500

# 500 visible, unexpired Listings get a stored score (0..100) for the Student.
SCORE_SEED = """
INSERT INTO app.match_scores (
    owner_id, listing_id, score, breakdown, algo_version, weights_version, vocab_version,
    computed_at
)
SELECT %(owner)s, id, (abs(hashtext(id::text)) %% 101), '{}'::jsonb, 1, 1, 'perf', now()
FROM app.listings
WHERE visibility_owner_id IS NULL AND expired_at IS NULL
ORDER BY first_seen_at DESC
LIMIT %(n)s
"""


@pytest.fixture
def seeded(migrated_db: str, accounts: AccountFactory) -> Iterator[tuple[MatchDb, list[uuid.UUID]]]:
    db = MatchDb(migrated_db)
    db.clear()
    owners = [accounts.create() for _ in range(3)]
    array = "ARRAY[" + ", ".join(f"'{owner}'" for owner in owners) + "]"
    sql = SEED.replace("{OWNERS}", array).replace("{RARE}", RARE_WORD)
    db.execute(sql.replace("{N}", str(POOL_SIZE)))
    ready, fresh = owners[0], owners[1]
    for owner in (ready, fresh):
        db.set_preferences(owner, ["Backend Intern", "Data Analyst"])
        db.set_profile(owner, ["python", "postgresql", "docker"])
    db.execute(SCORE_SEED, {"owner": ready, "n": SCORES})
    db.execute("ANALYZE app.listings")
    db.execute("ANALYZE app.match_scores")
    yield db, owners
    db.clear()


async def _feed(
    engine: AsyncEngine,
    owner: uuid.UUID,
    *,
    cursor: str | None = None,
    below: bool = False,
) -> service.FeedPage:
    async with AsyncSession(engine) as session:
        page = await service.feed(
            session, owner, limit=20, cursor=cursor, show_below_threshold=below
        )
        await session.rollback()  # the provisional path must store nothing
    return page


async def _best_of(engine: AsyncEngine, owner: uuid.UUID, **kwargs: object) -> tuple[float, int]:
    await _feed(engine, owner, **kwargs)  # warm-up
    best = float("inf")
    count = 0
    for _ in range(RUNS):
        start = time.perf_counter()
        page = await _feed(engine, owner, **kwargs)
        best = min(best, time.perf_counter() - start)
        count = len(page.items)
    return best, count


async def test_feed_pages_under_2_s_at_30k_listings(
    engine: AsyncEngine, seeded: tuple[MatchDb, list[uuid.UUID]]
) -> None:
    db, owners = seeded
    ready, fresh = owners[0], owners[1]
    assert db.count() == POOL_SIZE
    assert len(db.scores(ready)) == SCORES

    # A deep page: follow the cursor (toggle on, so all 500 are reachable) to page 20.
    cursor: str | None = None
    for _ in range(19):
        page = await _feed(engine, ready, cursor=cursor, below=True)
        assert page.state == "ready" and page.next_cursor is not None
        cursor = page.next_cursor

    timings: dict[str, float] = {}
    before = await calibrate(engine)
    best, count = await _best_of(engine, ready)
    timings["first page"], first_count = best, count
    assert first_count > 0
    timings["deep page"], count = await _best_of(engine, ready, cursor=cursor, below=True)
    assert count == 20
    timings["toggle on"], count = await _best_of(engine, ready, below=True)
    assert count == 20

    cap = get_settings().feed_on_request_cap
    async with AsyncSession(engine) as session:
        page = await service.feed(session, fresh, limit=50, cursor=None, show_below_threshold=True)
        await session.rollback()
    assert page.state == "provisional"
    assert len(page.items) == min(50, cap)
    timings["provisional"], count = await _best_of(engine, fresh)
    assert db.scores(fresh) == {}

    print({name: f"{secs * 1000:.0f} ms" for name, secs in timings.items()})
    budget = await budget_for(engine, BUDGET_SECONDS, earlier=before)
    for name, secs in timings.items():
        assert secs < budget.seconds, f"{name} took {secs * 1000:.0f} ms ({budget.describe()})"
