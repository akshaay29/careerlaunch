"""Load-robust performance budgets (NFR-1, NFR-2, Story 4.4).

CI is the authority: with ``CI`` set to true, 1 or yes (any case; GitHub Actions sets
``CI=true``) or ``PERF_STRICT=1`` the strict
budget applies as written. Anywhere else a calibration query (a trivial indexed ``SELECT`` on
the same seeded database, repeated) measures how slow this machine is right now, and the
budget is multiplied by ``local calibration / CI_REFERENCE_SECONDS``, clamped to 1-4x. A busy
laptop then gets more room, while a query that really got slower still fails. Timings are
always printed.
"""

import os
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

# Best-of-5 time of the calibration (CALIBRATION_QUERIES primary-key lookups) on a GitHub
# Actions ubuntu-latest runner against its postgres:17 service. A machine that takes longer
# than this is "slower than CI" by that ratio. PROVISIONAL: estimated locally; replace it with
# the "perf calibration:" value printed in a CI log (it only changes local budgets).
CI_REFERENCE_SECONDS = 0.025
CALIBRATION_QUERIES = 50
CALIBRATION_RUNS = 5
# ``CI`` values (any case) that mean "this is CI": GitHub Actions sets ``CI=true``.
CI_VALUES = ("true", "1", "yes")
MIN_FACTOR = 1.0
MAX_FACTOR = 4.0

_LOOKUP = text("SELECT id FROM app.listings WHERE id = :id")
_ANY_ID = text("SELECT id FROM app.listings ORDER BY id LIMIT 1")


def strict() -> bool:
    """Whether the strict (CI) budgets apply."""
    return os.environ.get("CI", "").strip().lower() in CI_VALUES or (
        os.environ.get("PERF_STRICT", "").strip() == "1"
    )


async def calibrate(engine: AsyncEngine) -> float:
    """Best-of-5 seconds for :data:`CALIBRATION_QUERIES` indexed lookups of one Listing."""
    best = float("inf")
    async with AsyncSession(engine) as session:
        listing_id = (await session.execute(_ANY_ID)).scalar() or uuid.UUID(int=0)
        await session.execute(_LOOKUP, {"id": listing_id})  # warm-up
        for _ in range(CALIBRATION_RUNS):
            start = time.perf_counter()
            for _ in range(CALIBRATION_QUERIES):
                await session.execute(_LOOKUP, {"id": listing_id})
            best = min(best, time.perf_counter() - start)
        await session.rollback()
    return best


@dataclass(frozen=True)
class Budget:
    base_seconds: float
    factor: float
    strict: bool
    calibration_seconds: float | None

    @property
    def seconds(self) -> float:
        return self.base_seconds * self.factor

    def describe(self) -> str:
        if self.strict:
            return f"strict budget {self.base_seconds * 1000:.0f} ms (CI=true or PERF_STRICT=1)"
        return (
            f"local budget {self.seconds * 1000:.0f} ms = {self.base_seconds * 1000:.0f} ms x "
            f"{self.factor:.2f} (calibration {self.calibration_seconds * 1000:.1f} ms vs CI "
            f"{CI_REFERENCE_SECONDS * 1000:.1f} ms)"
        )


def scaled(base_seconds: float, calibration_seconds: float | None) -> Budget:
    """The budget to assert: ``base_seconds`` when :func:`strict`, else ``base_seconds``
    times the clamped slowdown factor (``calibration_seconds`` is required then)."""
    if strict():
        return Budget(base_seconds, 1.0, True, calibration_seconds)
    if calibration_seconds is None:
        raise ValueError("a local budget needs a calibration time")
    factor = min(max(calibration_seconds / CI_REFERENCE_SECONDS, MIN_FACTOR), MAX_FACTOR)
    return Budget(base_seconds, factor, False, calibration_seconds)


async def budget_for(
    engine: AsyncEngine, base_seconds: float, *, earlier: float | None = None
) -> Budget:
    """:func:`scaled` with a fresh calibration (measured even in strict mode, for the log).
    ``earlier`` is a calibration taken before the timed work; the slower of the two counts,
    so a machine that was busy while the queries ran is not judged by a calm moment after."""
    calibration = await calibrate(engine)
    if earlier is not None:
        calibration = max(calibration, earlier)
    budget = scaled(base_seconds, calibration)
    # One greppable line: set CI_REFERENCE_SECONDS from this value in a CI log.
    print(
        f"perf calibration: {calibration:.4f} s for {CALIBRATION_QUERIES} lookups "
        f"(CI_REFERENCE_SECONDS = {CI_REFERENCE_SECONDS})"
    )
    print(budget.describe())
    return budget
