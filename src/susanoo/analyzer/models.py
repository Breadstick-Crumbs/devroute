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

    def level(self, dimension: str) -> int:
        return self.dimensions[dimension].level

    def signal_ids(self) -> set[str]:
        return {s.id for s in self.signals}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for name, dim in self.dimensions.items():
            data["dimensions"][name]["label"] = dim.label
        return data
