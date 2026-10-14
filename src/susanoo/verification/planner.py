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
    # How to perform and record the check, from the rule's `instructions` (manual checks).
    instructions: str = ""

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
    assessment: Assessment,
    profile: str,
    controls: list[Control],
    config: Config,
    ui_domain: str | None = None,
) -> list[SelectedStep]:
    """Pick verification rules and their level from profile, risk escalations, and controls.

    A rule with `ui_domains` applies only when the task's UI domain is one of them.
    """
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
        rule_domains = rule.get("ui_domains")
        if rule_domains and ui_domain not in rule_domains and rule["id"] not in control_steps:
            continue
        base = rule.get("levels", {}).get(profile)
        if base:
            level = base
            reasons.append(f"{profile.upper()} profile: {base}")
        for esc in rule.get("escalate", []):
            if "signal" in esc:
                if esc["signal"] in signals:
                    level = _raise(level, esc["level"])
                    reasons.append(f"signal {esc['signal']}: {esc['level']}")
                continue
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


def drop_toolless_optional(
    selected: list[SelectedStep], requirements: list[CapabilityRequirement], usable: set[str]
) -> tuple[list[SelectedStep], set[str]]:
    """Leave out `only_with_tool` steps whose capability has no usable tool.

    Such a step (visual regression needs screenshot baselines) is only worth doing when the project
    already has the tool; otherwise it would only add a gap. A required step, and a capability that
    something else asks for (another step, a signal, the profile), are kept. Returns the steps and
    the dropped capabilities.
    """
    optional = {s.rule["capability"] for s in selected if s.rule.get("only_with_tool")}
    # A required step (its profile level, an escalation, or a safety control) is never left out.
    elsewhere = {
        s.rule["capability"] for s in selected if not s.rule.get("only_with_tool") or s.level == "required"
    }
    elsewhere |= {
        r.capability for r in requirements if any(not x.startswith("verification: ") for x in r.reasons)
    }
    drop = optional - usable - elsewhere
    return [s for s in selected if s.rule["capability"] not in drop], drop


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
            instructions=str(rule.get("instructions", "")),
        )
        if rec is not None and rec.selected is not None:
            step.tool = rec.selected.name
            reported_only = rule.get("evidence") == "reported"
            step.command = None if reported_only else rec.selected.command
            step.mode = "ready" if step.command else "manual"
            step.withheld = "" if reported_only else rec.selected.command_withheld
            if rule["capability"] == "code_review":
                step.notes.append(
                    "Delegate to the susanoo-reviewer subagent (separate context) or a human reviewer."
                )
        elif rec is not None and rec.suggestion is not None:
            step.withheld = rec.suggestion.command_withheld
            step.notes.append(
                f"No usable tool; option: {rec.suggestion.name} ({rec.suggestion.status.value})."
            )
        else:
            step.notes.append("No usable tool detected; verify manually or close the gap first.")
        steps.append(step)
    steps.sort(key=lambda s: -_LEVEL_RANK[s.level])
    return VerificationPlan(profile=profile, steps=steps)
