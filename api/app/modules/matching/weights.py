"""matching.weights: the versioned, immutable Match Score weights (AD-9).

Never edit a version: a change adds a new one and bumps :data:`CURRENT_WEIGHTS_VERSION`.
:data:`ALGO_VERSION` changes whenever the scoring formula in :mod:`.scoring` changes.
"""

from dataclasses import dataclass
from types import MappingProxyType

ALGO_VERSION = 1
# How far skill + text may be from 1 (float rounding).
SUM_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Weights:
    """The share of the 0-100 score given to skill overlap and to text similarity. Per-skill
    weights are equal."""

    version: int
    skill: float
    text: float

    def __post_init__(self) -> None:
        if not (0 <= self.skill <= 1 and 0 <= self.text <= 1):
            raise ValueError("weights must each be between 0 and 1")
        if abs(self.skill + self.text - 1) > SUM_TOLERANCE:
            raise ValueError("skill and text weights must sum to 1")


WEIGHTS: MappingProxyType[int, Weights] = MappingProxyType(
    {1: Weights(version=1, skill=0.7, text=0.3)}
)
CURRENT_WEIGHTS_VERSION = 1


def current_weights() -> Weights:
    return WEIGHTS[CURRENT_WEIGHTS_VERSION]
