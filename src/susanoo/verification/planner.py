"""Verification Intelligence: decide which checks a task needs and how to run them."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from susanoo.analyzer.models import Assessment, CapabilityRequirement
from susanoo.config import Config
from susanoo.router import Control
from susanoo.selection import ToolRecommendation

_LEVEL_RANK = {"recommended": 1, "required": 2}


@dataclass
class VerificationStep:
    id: str
    title: str
    capability: str
    level: str  # required | recommended
    reasons: list[str]
    tool: str | None = None
    command: list[str] | None = None
    # ready: Susanoo can run it (`susanoo verify --execute`)
    # manual: a usable tool exists but the step is performed by the agent or a person
    # unavailable: no usable tool; this is a capability gap
    mode: str = "unavailable"
    notes: list[str] = field(default_factory=list)
    # Why a tool with a run command is manual here (e.g. its Python environment is not reachable).
    withheld: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationPlan:
    profile: str
    steps: list[VerificationStep]

    def get(self, step_id: str) -> VerificationStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "steps": [s.to_dict() for s in self.steps]}


@dataclass
class SelectedStep:
    rule: dict[str, Any]
    level: str
    reasons: list[str]


def _raise(current: str | None, new: str) -> str:
    if current is None or _LEVEL_RANK[new] > _LEVEL_RANK[current]:
        return new
    return current


def select_steps(
    assessment: Assessment, profile: str, controls: list[Control], config: Config
) -> list[SelectedStep]:
    """Pick verification rules and their level from profile, risk escalations, and controls."""
    signals = assessment.signal_ids()
    control_steps: dict[str, list[str]] = {}
    for control in controls:
        for step_id in control.verification:
            control_steps.setdefault(step_id, []).append(control.id)

    selected: list[SelectedStep] = []
    for rule in config.get("verification", []):
        level: str | None = None
        reasons: list[str] = []
        required_signals = rule.get("signals")
        if required_signals and not signals & set(required_signals) and rule["id"] not in control_steps:
            continue
        base = rule.get("levels", {}).get(profile)
        if base:
            level = base
            reasons.append(f"{profile.upper()} profile: {base}")
        for esc in rule.get("escalate", []):
            dim = esc["dimension"]
            if assessment.level(dim) >= esc["min"]:
                level = _raise(level, esc["level"])
                reasons.append(f"{dim} is {assessment.dimensions[dim].label}: {esc['level']}")
        if rule["id"] in control_steps:
            level = "required"
            reasons.append("mandatory control: " + ", ".join(control_steps[rule["id"]]))
        if level is not None:
            selected.append(SelectedStep(rule, level, reasons))
    return selected


def verification_requirements(selected: list[SelectedStep]) -> list[CapabilityRequirement]:
    reqs: dict[str, CapabilityRequirement] = {}
    for item in selected:
        capability = item.rule["capability"]
        req = reqs.setdefault(capability, CapabilityRequirement(capability, item.level))
        if item.level == "required":
            req.level = "required"
        req.reasons.append(f"verification: {item.rule.get('title', item.rule['id'])}")
    return list(reqs.values())


def build_plan(
    profile: str, selected: list[SelectedStep], recommendations: list[ToolRecommendation]
) -> VerificationPlan:
    by_capability = {r.capability: r for r in recommendations}
    steps: list[VerificationStep] = []
    for item in selected:
        rule = item.rule
        rec = by_capability.get(rule["capability"])
        step = VerificationStep(
            id=rule["id"],
            title=rule.get("title", rule["id"]),
            capability=rule["capability"],
            level=item.level,
            reasons=item.reasons,
        )
        if rec is not None and rec.selected is not None:
            step.tool = rec.selected.name
            step.command = rec.selected.command
            step.mode = "ready" if rec.selected.command else "manual"
            step.withheld = rec.selected.command_withheld
            if rule["capability"] == "code_review":
                step.notes.append(
                    "Delegate to the susanoo-reviewer subagent (separate context) or a human reviewer."
                )
        elif rec is not None and rec.suggestion is not None:
            step.notes.append(
                f"No usable tool; option: {rec.suggestion.name} ({rec.suggestion.status.value})."
            )
        else:
            step.notes.append("No usable tool detected; verify manually or close the gap first.")
        steps.append(step)
    steps.sort(key=lambda s: -_LEVEL_RANK[s.level])
    return VerificationPlan(profile=profile, steps=steps)
