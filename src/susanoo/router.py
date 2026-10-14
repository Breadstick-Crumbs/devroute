"""Adaptive Workflow Router: selects FAST, STANDARD, or DEEP and the safety controls that apply."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from susanoo.analyzer.models import Assessment
from susanoo.config import PROFILE_ORDER, Config, ConfigError, load_toml_data, load_toml_file, merge
from susanoo.paths import state_dir


@dataclass
class Phase:
    id: str
    title: str
    action: str
    approval: bool = False


@dataclass
class Profile:
    name: str
    title: str
    summary: str
    intended_for: str
    planning: str
    delegation: str
    review: str
    isolation: str
    verification_depth: str
    report: str
    phases: list[Phase]
    approval_checkpoints: list[str]
    capabilities: list[dict[str, str]]


@dataclass
class Control:
    """A mandatory safety control triggered by the assessment; survives profile overrides."""

    id: str
    description: str
    checkpoint: str
    reason: str
    verification: list[str] = field(default_factory=list)


@dataclass
class RouteDecision:
    profile: str
    recommended: str
    reasons: list[str]
    overridden: bool
    override_notes: list[str]
    controls: list[Control]
    checkpoints: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rank(profile: str) -> int:
    return PROFILE_ORDER.index(profile)


def load_profiles(root: Path | None = None) -> dict[str, Profile]:
    """Load the built-in profiles, merged with ``.susanoo/profiles/<name>.toml`` overrides."""
    profiles: dict[str, Profile] = {}
    for name in PROFILE_ORDER:
        data = load_toml_data("profiles", f"{name}.toml")
        if root is not None:
            override = state_dir(root) / "profiles" / f"{name}.toml"
            if override.is_file():
                data = merge(data, load_toml_file(override))
        profiles[name] = _build_profile(data)
    return profiles


def _build_profile(data: dict[str, Any]) -> Profile:
    try:
        phases = [
            Phase(p["id"], p["title"], p["action"], bool(p.get("approval", False))) for p in data["phases"]
        ]
        return Profile(
            name=data["name"],
            title=data["title"],
            summary=data["summary"],
            intended_for=data["intended_for"],
            planning=data["planning"],
            delegation=data["delegation"],
            review=data["review"],
            isolation=data["isolation"],
            verification_depth=data["verification_depth"],
            report=data["report"],
            phases=phases,
            approval_checkpoints=list(data.get("approval_checkpoints", [])),
            capabilities=list(data.get("capabilities", [])),
        )
    except KeyError as exc:
        raise ConfigError(f"profile '{data.get('name', '?')}' is missing field {exc}") from exc


def recommended_profile(assessment: Assessment) -> str:
    """The profile the routing rules select for ``assessment``, before any override."""
    return _recommend(assessment)[0]


def _recommend(assessment: Assessment) -> tuple[str, list[str]]:
    """Apply the routing rules; the strictest profile any rule selects wins."""
    complexity = assessment.complexity.level
    risk = assessment.risk.level
    uncertainty = assessment.uncertainty.level
    fired: list[tuple[str, str]] = []

    if risk == "critical":
        fired.append(("deep", "risk is critical"))
    if complexity == "high":
        fired.append(("deep", "complexity is high"))
    if uncertainty == "high" and complexity != "low":
        fired.append(("deep", "uncertainty is high on a non-trivial change"))
    if risk == "high":
        fired.append(("standard", "risk is high"))
    if complexity == "moderate":
        fired.append(("standard", "complexity is moderate"))
    if risk == "moderate":
        fired.append(("standard", "risk is moderate"))
    if uncertainty in ("moderate", "high"):
        fired.append(("standard", f"uncertainty is {uncertainty}: investigate before changing code"))

    if not fired:
        return "fast", ["complexity, risk, and uncertainty are all low"]
    profile = max((p for p, _ in fired), key=rank)
    reasons = [reason for p, reason in fired if p == profile]
    reasons += [f"(also: {reason})" for p, reason in fired if p != profile]
    return profile, reasons


def _controls(assessment: Assessment, config: Config) -> list[Control]:
    controls: list[Control] = []
    for entry in config.get("controls", []):
        dim = entry["when"]["dimension"]
        minimum = entry["when"]["min"]
        level = assessment.level(dim)
        if level >= minimum:
            controls.append(
                Control(
                    id=entry["id"],
                    description=entry["description"],
                    checkpoint=entry.get("checkpoint", ""),
                    reason=f"{dim} is {assessment.dimensions[dim].label}",
                    verification=list(entry.get("verification", [])),
                )
            )
    return controls


def route(
    assessment: Assessment,
    profiles: dict[str, Profile],
    config: Config,
    override: str | None = None,
    minimum: str | None = None,
) -> RouteDecision:
    """Choose a profile. ``override`` changes the profile but never removes safety controls.

    ``minimum`` raises the recommendation (the user's message alone routes at least that high);
    it never lowers it.
    """
    recommended, reasons = _recommend(assessment)
    if minimum in PROFILE_ORDER and rank(minimum) > rank(recommended):
        own = (
            []
            if recommended == "fast"
            else [r if r.startswith("(also: ") else f"(also: {r})" for r in reasons]
        )
        reasons = [f"the user's message alone routes {minimum.upper()}", *own]
        recommended = minimum
    profile = recommended
    notes: list[str] = []

    if override is not None:
        override = override.lower()
        if override not in PROFILE_ORDER:
            raise ValueError(f"unknown profile '{override}' (choose from {', '.join(PROFILE_ORDER)})")
        profile = override
        if override != recommended:
            direction = "below" if rank(override) < rank(recommended) else "above"
            notes.append(
                f"Profile overridden to {override.upper()} ({direction} the recommended {recommended.upper()})."
            )
        floor = config.get("routing", {}).get("critical_risk_floor", "fast")
        if assessment.risk.level == "critical" and rank(profile) < rank(floor):
            notes.append(
                f"Risk is critical: {override.upper()} was raised to {floor.upper()} "
                "(routing.critical_risk_floor)."
            )
            profile = floor

    controls = _controls(assessment, config)
    if controls and override is not None and rank(profile) < rank(recommended):
        notes.append("Mandatory safety controls still apply: " + ", ".join(c.id for c in controls) + ".")

    checkpoints = list(profiles[profile].approval_checkpoints)
    for control in controls:
        if control.checkpoint and control.checkpoint not in checkpoints:
            checkpoints.append(control.checkpoint)
    if assessment.confidence == "low":
        checkpoints.insert(
            0, "Confirm the clarified task scope before starting (the assessment had low confidence)."
        )

    return RouteDecision(
        profile=profile,
        recommended=recommended,
        reasons=reasons,
        overridden=override is not None and override != recommended,
        override_notes=notes,
        controls=controls,
        checkpoints=checkpoints,
    )
