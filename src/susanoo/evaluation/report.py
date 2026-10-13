"""Summaries of local records and reviewable improvement recommendations.

Recommendations are text for a human to review. Nothing here changes configuration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

OUTCOME_STATUSES = ("completed", "partial", "failed", "abandoned")


@dataclass
class ProfileSummary:
    profile: str
    routes: int = 0
    overridden: int = 0
    with_outcome: int = 0
    completed: int = 0
    failed_or_abandoned: int = 0
    regressions: int = 0
    mean_minutes: float | None = None
    mean_interventions: float | None = None
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    executed_checks: int = 0
    executed_failures: int = 0
    reported_checks: int = 0

    @property
    def completion_rate(self) -> float | None:
        return self.completed / self.with_outcome if self.with_outcome else None

    @property
    def check_failure_rate(self) -> float | None:
        return self.executed_failures / self.executed_checks if self.executed_checks else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["completion_rate"] = self.completion_rate
        data["check_failure_rate"] = self.check_failure_rate
        return data


@dataclass
class Insight:
    profile: str
    observation: str
    suggestion: str
    sample_size: int


@dataclass
class Report:
    profiles: list[ProfileSummary]
    insights: list[Insight]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profiles": [p.to_dict() for p in self.profiles],
            "insights": [asdict(i) for i in self.insights],
            "notes": self.notes,
        }


def latest_by_record(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        record_id = item.get("record_id")
        if isinstance(record_id, str):
            latest[record_id] = item
    return latest


def summarize(
    routes: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    min_samples: int = 10,
) -> Report:
    outcome_by_id = latest_by_record(outcomes)
    evidence_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        evidence_by_id.setdefault(str(item.get("record_id")), []).append(item)

    summaries: dict[str, ProfileSummary] = {}
    minutes: dict[str, list[float]] = {}
    interventions: dict[str, list[float]] = {}
    overrides: dict[str, list[str]] = {}

    for route in routes:
        profile = str(route.get("profile", "unknown"))
        summary = summaries.setdefault(profile, ProfileSummary(profile))
        summary.routes += 1
        if route.get("overridden"):
            summary.overridden += 1
            overrides.setdefault(profile, []).append(str(route.get("recommended")))
        for item in evidence_by_id.get(str(route.get("id")), []):
            if item.get("source") == "executed" and item.get("status") in (
                "passed",
                "failed",
                "error",
                "timeout",
            ):
                summary.executed_checks += 1
                if item.get("status") != "passed":
                    summary.executed_failures += 1
            elif item.get("source") == "reported":
                summary.reported_checks += 1
        outcome = outcome_by_id.get(str(route.get("id")))
        if outcome is None:
            continue
        summary.with_outcome += 1
        status = outcome.get("status")
        if status == "completed":
            summary.completed += 1
        elif status in ("failed", "abandoned"):
            summary.failed_or_abandoned += 1
        summary.regressions += int(outcome.get("regressions") or 0)
        if isinstance(outcome.get("duration_minutes"), int | float):
            minutes.setdefault(profile, []).append(float(outcome["duration_minutes"]))
        if isinstance(outcome.get("human_interventions"), int):
            interventions.setdefault(profile, []).append(float(outcome["human_interventions"]))
        if isinstance(outcome.get("tokens"), int):
            summary.total_tokens = (summary.total_tokens or 0) + outcome["tokens"]
        if isinstance(outcome.get("cost_usd"), int | float):
            summary.total_cost_usd = round((summary.total_cost_usd or 0.0) + float(outcome["cost_usd"]), 4)

    for profile, summary in summaries.items():
        if minutes.get(profile):
            summary.mean_minutes = round(mean(minutes[profile]), 1)
        if interventions.get(profile):
            summary.mean_interventions = round(mean(interventions[profile]), 2)

    ordered = [summaries[p] for p in ("fast", "standard", "deep") if p in summaries]
    ordered += [s for p, s in summaries.items() if p not in ("fast", "standard", "deep")]
    insights, notes = _insights(ordered, overrides, min_samples)
    return Report(profiles=ordered, insights=insights, notes=notes)


def _insights(
    summaries: list[ProfileSummary], overrides: dict[str, list[str]], min_samples: int
) -> tuple[list[Insight], list[str]]:
    insights: list[Insight] = []
    notes: list[str] = []
    for s in summaries:
        if s.with_outcome < min_samples:
            notes.append(
                f"{s.profile.upper()}: {s.with_outcome} task(s) with recorded outcomes; "
                f"at least {min_samples} are needed before Susanoo suggests changes."
            )
            continue
        rate = s.completion_rate or 0.0
        if rate < 0.7:
            insights.append(
                Insight(
                    s.profile,
                    f"Only {rate:.0%} of {s.profile.upper()} tasks completed.",
                    "Review the failed tasks; if they share signals, raise those signal effects or lower "
                    "the complexity/risk thresholds so similar tasks route to a deeper profile.",
                    s.with_outcome,
                )
            )
        if s.regressions and s.regressions / s.with_outcome >= 0.2:
            insights.append(
                Insight(
                    s.profile,
                    f"{s.regressions} regression(s) reported across {s.with_outcome} {s.profile.upper()} tasks.",
                    "Consider making integration tests or independent review required for this profile.",
                    s.with_outcome,
                )
            )
        if s.profile == "fast" and (s.mean_interventions or 0) > 2:
            insights.append(
                Insight(
                    s.profile,
                    f"FAST tasks needed {s.mean_interventions} human interventions on average.",
                    "FAST may be under-planning for this project; consider routing similar tasks to STANDARD.",
                    s.with_outcome,
                )
            )
    for s in summaries:
        if s.routes >= min_samples and s.overridden / s.routes >= 0.4:
            sources = overrides.get(s.profile, [])
            common = max(set(sources), key=sources.count) if sources else "?"
            insights.append(
                Insight(
                    s.profile,
                    f"{s.overridden} of {s.routes} tasks were overridden to {s.profile.upper()} "
                    f"(most often from {common.upper()}).",
                    "Routing thresholds may be miscalibrated for this project; compare the overridden tasks' "
                    "assessments and adjust thresholds or signals in .susanoo/config.toml.",
                    s.routes,
                )
            )
    return insights, notes
