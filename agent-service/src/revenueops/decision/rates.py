"""Measured success rates, replacing the hand-set starting estimates in priors.py once there's evidence.

A RateTable holds, per (action, case type), how many times the action was tried and how often the
revenue came back. The no-action baseline is stored under the action key NO_ACTION. The scorer uses a
measured rate only when it rests on at least `min_trials` trials; otherwise it falls back to the
starting estimate and says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

NO_ACTION = "none"
MIN_TRIALS = 30


@dataclass(frozen=True)
class Rate:
    successes: int
    trials: int

    @property
    def p(self) -> float:
        return self.successes / self.trials if self.trials else 0.0

    def interval(self, z: float = 1.96) -> tuple[float, float]:
        """Wilson score interval: honest for small samples and rates near 0 or 1."""
        n = self.trials
        if n == 0:
            return (0.0, 1.0)
        p = self.p
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class RateTable:
    rates: Mapping[tuple[str, str], Rate] = field(default_factory=dict)  # (action or NO_ACTION, case type)
    source: str = ""  # where the numbers came from, e.g. "calibration 3: simulation, seed 7"
    min_trials: int = MIN_TRIALS

    def get(self, action: str, case_type: str) -> Rate | None:
        """The measured rate, if there is enough evidence to use it."""
        rate = self.rates.get((action, case_type))
        return rate if rate is not None and rate.trials >= self.min_trials else None
