"""Data types produced by the Task Intelligence Engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LEVEL_NAMES: tuple[str, ...] = ("none", "low", "moderate", "high")


@dataclass
class DimensionScore:
    """One of the ten assessment dimensions, scored 0 (none) to 3 (high)."""

    name: str
    level: int
    reasons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LEVEL_NAMES[self.level]


@dataclass
class SignalMatch:
    """A configured signal whose patterns matched the task description."""

    id: str
    label: str
    matches: list[str]
    area: str | None = None
    kind: str | None = None


@dataclass
class CompositeScore:
    """An aggregate (complexity, risk, or uncertainty) with its drivers."""

    level: str
    score: float
    drivers: list[str] = field(default_factory=list)


@dataclass
class CapabilityRequirement:
    """A capability the task needs, with the strongest level any source asked for."""

    capability: str
    level: str  # "required" | "recommended"
    reasons: list[str] = field(default_factory=list)


@dataclass
class ProjectContext:
    """Optional repository facts that adjust the assessment. ``None`` means unknown."""

    has_test_framework: bool | None = None
    is_git_repo: bool | None = None
    source_files: int | None = None


@dataclass
class Floor:
    """A minimum for an assessment: the user's message, or a route that is being replaced.

    When Claude routes a task in its own words, the result is never lower than the user's
    message alone: every dimension takes the stricter level, the user's signals and areas are
    kept, and the profile is at least ``profile``. Only levels and signal ids are kept, never
    the message text. Levels are taken before project context is applied, so context counts once.
    """

    @classmethod
    def merge(cls, floors: list[Floor | None], order: tuple[str, ...]) -> Floor | None:
        """The strictest of ``floors`` on every part (``order`` ranks profiles)."""
        present = [f for f in floors if f is not None]
        if not present:
            return None
        levels: dict[str, int] = {}
        signals: list[str] = []
        areas: list[str] = []
        for f in present:
            for dim, level in f.levels.items():
                levels[dim] = max(levels.get(dim, 0), level)
            signals += [s for s in f.signals if s not in signals]
            areas += [a for a in f.areas if a not in areas]
        known = [f.profile for f in present if f.profile in order]
        profile = max(known, key=order.index) if known else order[0]
        return cls(levels, signals, areas, profile)

    levels: dict[str, int]
    signals: list[str]
    areas: list[str]
    profile: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Floor | None:
        """A floor from stored JSON, or ``None`` when it is missing or malformed."""
        if not isinstance(data, dict):
            return None
        levels, signals, areas, profile = (data.get(k) for k in ("levels", "signals", "areas", "profile"))
        if not (
            isinstance(levels, dict)
            and all(isinstance(k, str) and type(v) is int and 0 <= v <= 3 for k, v in levels.items())
            and isinstance(signals, list)
            and all(isinstance(s, str) for s in signals)
            and isinstance(areas, list)
            and all(isinstance(a, str) for a in areas)
            and isinstance(profile, str)
        ):
            return None
        return cls(dict(levels), list(signals), list(areas), profile)


@dataclass
class Assessment:
    task: str
    dimensions: dict[str, DimensionScore]
    complexity: CompositeScore
    risk: CompositeScore
    uncertainty: CompositeScore
    confidence: str  # "low" | "moderate" | "high"
    confidence_notes: list[str]
    signals: list[SignalMatch]
    areas: list[str]
    requirements: list[CapabilityRequirement]
    context_notes: list[str] = field(default_factory=list)
    negated: list[str] = field(default_factory=list)  # phrases ignored as "do not change"
    # Levels before project context (after any floor): what a later floor is built from.
    base_levels: dict[str, int] = field(default_factory=dict)

    def level(self, dimension: str) -> int:
        return self.dimensions[dimension].level

    def signal_ids(self) -> set[str]:
        return {s.id for s in self.signals}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for name, dim in self.dimensions.items():
            data["dimensions"][name]["label"] = dim.label
        return data
