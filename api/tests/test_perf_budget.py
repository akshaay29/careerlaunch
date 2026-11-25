"""Story 4.4: the performance budgets are strict in CI and calibrated elsewhere."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests import perf_budget
from tests.perf_budget import CI_REFERENCE_SECONDS, MAX_FACTOR, calibrate, scaled, strict
from tests.test_feed_perf import BUDGET_SECONDS as FEED_BUDGET
from tests.test_listing_search_perf import BUDGET_SECONDS as SEARCH_BUDGET


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PERF_STRICT", raising=False)


def test_the_written_budgets_are_the_epic_targets() -> None:
    assert SEARCH_BUDGET == 0.5
    assert FEED_BUDGET == 2.0


@pytest.mark.parametrize(("var", "value"), [("CI", "true"), ("CI", "TRUE"), ("CI", "1"), ("CI", "yes"), ("CI", "Yes"), ("PERF_STRICT", "1")])
def test_ci_takes_the_strict_branch(
    monkeypatch: pytest.MonkeyPatch, local: None, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    assert strict()
    # Even on a machine 10x slower than CI, the budget is not loosened.
    budget = scaled(SEARCH_BUDGET, CI_REFERENCE_SECONDS * 10)
    assert budget.strict and budget.factor == 1.0 and budget.seconds == SEARCH_BUDGET
    assert scaled(FEED_BUDGET, None).seconds == FEED_BUDGET


@pytest.mark.usefixtures("local")
def test_locally_the_budget_scales_with_the_calibration_clamped_to_1_to_4x() -> None:
    assert not strict()
    assert scaled(SEARCH_BUDGET, CI_REFERENCE_SECONDS / 3).seconds == SEARCH_BUDGET  # never tighter
    assert scaled(SEARCH_BUDGET, CI_REFERENCE_SECONDS * 2).seconds == pytest.approx(1.0)
    assert scaled(FEED_BUDGET, CI_REFERENCE_SECONDS * 50).seconds == FEED_BUDGET * MAX_FACTOR
    with pytest.raises(ValueError):
        scaled(SEARCH_BUDGET, None)


@pytest.mark.usefixtures("local")
def test_other_ci_values_are_not_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "false")
    monkeypatch.setenv("PERF_STRICT", "0")
    assert not strict()


async def test_the_calibration_measures_something(engine: AsyncEngine) -> None:
    seconds = await calibrate(engine)
    assert 0 < seconds < 60
    print(f"calibration {seconds * 1000:.1f} ms (CI reference {CI_REFERENCE_SECONDS * 1000:.1f} ms)")
    assert perf_budget.CALIBRATION_QUERIES >= 10
