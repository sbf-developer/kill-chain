"""Latency primitives.

Every activity in a kill chain -- a sensor dwell, an analyst's review, a
commander's decision, a handoff across a link -- is modelled as a random
duration. Seconds are the unit everywhere.

The distributions are deliberately boring. Lognormal is the workhorse: real
task times are right-skewed (fast typical case, long tail when something goes
wrong), and summing a few lognormals reproduces the fat right tail that
actually kills targeting cycles.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any


class SpecError(ValueError):
    """Raised when a scenario file contains an invalid distribution spec."""


class Distribution:
    """Base class. Subclasses are frozen dataclasses and therefore hashable."""

    def sample(self, rng: random.Random) -> float:
        raise NotImplementedError

    def mean(self) -> float:
        raise NotImplementedError

    def scaled(self, factor: float) -> "Distribution":
        """Return a copy of this distribution with durations multiplied."""
        return Scaled(self, factor)

    def describe(self) -> str:
        return self.__class__.__name__


@dataclass(frozen=True)
class Constant(Distribution):
    value: float

    def sample(self, rng: random.Random) -> float:
        return self.value

    def mean(self) -> float:
        return self.value

    def describe(self) -> str:
        return f"{self.value:g}s"


@dataclass(frozen=True)
class Uniform(Distribution):
    lo: float
    hi: float

    def sample(self, rng: random.Random) -> float:
        return self.lo + (self.hi - self.lo) * rng.random()

    def mean(self) -> float:
        return (self.lo + self.hi) / 2.0

    def describe(self) -> str:
        return f"U({self.lo:g}-{self.hi:g})s"


@dataclass(frozen=True)
class Triangular(Distribution):
    lo: float
    mode: float
    hi: float

    def sample(self, rng: random.Random) -> float:
        return rng.triangular(self.lo, self.hi, self.mode)

    def mean(self) -> float:
        return (self.lo + self.mode + self.hi) / 3.0

    def describe(self) -> str:
        return f"T({self.lo:g}/{self.mode:g}/{self.hi:g})s"


@dataclass(frozen=True)
class LogNormal(Distribution):
    """Parametrised by the median (seconds) and sigma of the underlying normal."""

    median: float
    sigma: float

    def sample(self, rng: random.Random) -> float:
        return self.median * math.exp(self.sigma * rng.gauss(0.0, 1.0))

    def mean(self) -> float:
        return self.median * math.exp(self.sigma * self.sigma / 2.0)

    def describe(self) -> str:
        return f"LN(med {self.median:g}s, s={self.sigma:g})"


@dataclass(frozen=True)
class Normal(Distribution):
    mean_s: float
    sd: float

    def sample(self, rng: random.Random) -> float:
        return max(0.0, rng.gauss(self.mean_s, self.sd))

    def mean(self) -> float:
        # Truncation at zero biases the true mean very slightly upward; the
        # error is negligible for the parameter ranges used here.
        return self.mean_s

    def describe(self) -> str:
        return f"N({self.mean_s:g}s, sd {self.sd:g})"


@dataclass(frozen=True)
class Exponential(Distribution):
    mean_s: float

    def sample(self, rng: random.Random) -> float:
        return rng.expovariate(1.0 / self.mean_s)

    def mean(self) -> float:
        return self.mean_s

    def describe(self) -> str:
        return f"Exp({self.mean_s:g}s)"


@dataclass(frozen=True)
class Beta(Distribution):
    """Used for classifier confidence, which is bounded to [0, 1]."""

    a: float
    b: float

    def sample(self, rng: random.Random) -> float:
        g1 = rng.gammavariate(self.a, 1.0)
        g2 = rng.gammavariate(self.b, 1.0)
        total = g1 + g2
        return g1 / total if total > 0 else 0.5

    def mean(self) -> float:
        return self.a / (self.a + self.b)

    def describe(self) -> str:
        return f"Beta({self.a:g},{self.b:g})"


@dataclass(frozen=True)
class Scaled(Distribution):
    inner: Distribution
    factor: float

    def sample(self, rng: random.Random) -> float:
        return self.inner.sample(rng) * self.factor

    def mean(self) -> float:
        return self.inner.mean() * self.factor

    def describe(self) -> str:
        return f"{self.inner.describe()} x{self.factor:g}"


def parse(raw: Any, where: str) -> Distribution:
    """Parse a scenario JSON fragment into a Distribution.

    Accepted forms::

        45                                  -> Constant(45)
        {"dist": "lognormal", "median": 40, "sigma": 0.6}
        {"dist": "triangular", "min": 5, "mode": 12, "max": 60}
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if raw < 0:
            raise SpecError(f"{where}: duration must be >= 0, got {raw}")
        return Constant(float(raw))
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: expected a number or an object, got {type(raw).__name__}")

    kind = raw.get("dist")
    if not isinstance(kind, str):
        raise SpecError(f"{where}: missing 'dist' key")
    kind = kind.lower()

    def num(key: str, required: bool = True, default: float = 0.0) -> float:
        if key not in raw:
            if required:
                raise SpecError(f"{where}: '{kind}' requires '{key}'")
            return default
        value = raw[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SpecError(f"{where}: '{key}' must be a number")
        return float(value)

    if kind == "constant":
        return Constant(num("value"))
    if kind == "uniform":
        lo, hi = num("min"), num("max")
        if hi < lo:
            raise SpecError(f"{where}: uniform max < min")
        return Uniform(lo, hi)
    if kind == "triangular":
        lo, mode, hi = num("min"), num("mode"), num("max")
        if not (lo <= mode <= hi):
            raise SpecError(f"{where}: triangular requires min <= mode <= max")
        return Triangular(lo, mode, hi)
    if kind in ("lognormal", "log-normal", "ln"):
        median, sigma = num("median"), num("sigma", required=False, default=0.5)
        if median <= 0:
            raise SpecError(f"{where}: lognormal median must be > 0")
        if sigma < 0:
            raise SpecError(f"{where}: lognormal sigma must be >= 0")
        return LogNormal(median, sigma)
    if kind in ("normal", "gaussian"):
        mean_s, sd = num("mean"), num("sd", required=False, default=1.0)
        if sd < 0:
            raise SpecError(f"{where}: normal sd must be >= 0")
        return Normal(mean_s, sd)
    if kind in ("exponential", "exp"):
        mean_s = num("mean")
        if mean_s <= 0:
            raise SpecError(f"{where}: exponential mean must be > 0")
        return Exponential(mean_s)
    if kind == "beta":
        a, b = num("a"), num("b")
        if a <= 0 or b <= 0:
            raise SpecError(f"{where}: beta shape parameters must be > 0")
        return Beta(a, b)
    raise SpecError(f"{where}: unknown distribution '{kind}'")
