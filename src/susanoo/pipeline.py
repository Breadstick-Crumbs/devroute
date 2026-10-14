"""End-to-end planning: assessment -> route -> capabilities -> tools -> gaps -> verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo import __version__
from susanoo.analyzer import Assessment, CapabilityRequirement, Floor, ProjectContext, assess
from susanoo.analyzer.engine import without_vagueness
from susanoo.config import Config
from susanoo.discovery import Inventory, build_inventory, has_test_framework
from susanoo.registry import Registry, load_registry
from susanoo.research import Gap, analyze_gaps
from susanoo.router import Profile, RouteDecision, load_profiles, recommended_profile, route
from susanoo.selection import SelectionContext, ToolRecommendation, recommend
from susanoo.verification import VerificationPlan, build_plan, select_steps, verification_requirements


@dataclass
class TaskPlan:
    assessment: Assessment
    decision: RouteDecision
    profile: Profile
    inventory: Inventory
    recommendations: list[ToolRecommendation]
    gaps: list[Gap]
    verification: VerificationPlan
    not_required: list[str]
    record_id: str | None = None
    hands_off: bool = False  # `[enforcement] hands_off`: checkpoints are reported, not waited at

    def to_dict(self, include_inventory: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "susanoo_version": __version__,
            "record_id": self.record_id,
            "assessment": self.assessment.to_dict(),
            "route": self.decision.to_dict(),
            "profile": {
                "name": self.profile.name,
                "title": self.profile.title,
                "summary": self.profile.summary,
                "planning": self.profile.planning,
                "delegation": self.profile.delegation,
                "review": self.profile.review,
                "isolation": self.profile.isolation,
                "verification_depth": self.profile.verification_depth,
                "phases": [vars(p) for p in self.profile.phases],
            },
            "capabilities": [r.to_dict() for r in self.recommendations],
            "gaps": [g.to_dict() for g in self.gaps],
            "verification": self.verification.to_dict(),
            "not_required": self.not_required,
            "hands_off": self.hands_off,
            "environment": {
                "root": self.inventory.root,
                "languages": self.inventory.languages,
                "verified": self.inventory.verified,
            },
        }
        if include_inventory:
            data["inventory"] = self.inventory.to_dict()
        return data


def merge_requirements(*groups: list[CapabilityRequirement]) -> list[CapabilityRequirement]:
    merged: dict[str, CapabilityRequirement] = {}
    for group in groups:
        for req in group:
            current = merged.setdefault(req.capability, CapabilityRequirement(req.capability, req.level))
            if req.level == "required":
                current.level = "required"
            for reason in req.reasons:
                if reason not in current.reasons:
                    current.reasons.append(reason)
    order = {"required": 0, "recommended": 1}
    return sorted(merged.values(), key=lambda r: (order[r.level], r.capability))


def project_context(inventory: Inventory) -> ProjectContext:
    return ProjectContext(
        has_test_framework=has_test_framework(inventory),
        is_git_repo=inventory.get("vcs:git-repository") is not None,
        source_files=inventory.source_files,
    )


def plan_task(
    task: str,
    root: Path,
    config: Config,
    *,
    override: str | None = None,
    registry: Registry | None = None,
    profiles: dict[str, Profile] | None = None,
    inventory: Inventory | None = None,
    verify_tools: bool = False,
    home: Path | None = None,
    floor: Floor | None = None,
) -> TaskPlan:
    registry = registry or load_registry(root)
    profiles = profiles or load_profiles(root)
    inventory = inventory or build_inventory(root, registry, verify=verify_tools, home=home)

    assessment = assess(task, config, project_context(inventory), floor=floor)
    decision = route(
        assessment, profiles, config, override=override, minimum=floor.profile if floor else None
    )
    profile = profiles[decision.profile]

    selected_steps = select_steps(assessment, decision.profile, decision.controls, config)
    profile_reqs = [
        CapabilityRequirement(
            c["id"], c.get("level", "recommended"), [f"{profile.title} profile: {c.get('reason', '')}"]
        )
        for c in profile.capabilities
    ]
    requirements = merge_requirements(
        assessment.requirements, profile_reqs, verification_requirements(selected_steps)
    )
    recommendations = recommend(
        requirements, inventory, registry, SelectionContext.from_config(config, task=task)
    )
    gaps = analyze_gaps(recommendations)
    verification = build_plan(decision.profile, selected_steps, recommendations)
    needed = {r.capability for r in requirements}
    not_required = sorted(c for c in registry.capabilities if c not in needed)

    return TaskPlan(
        assessment=assessment,
        decision=decision,
        profile=profile,
        inventory=inventory,
        recommendations=recommendations,
        gaps=gaps,
        verification=verification,
        not_required=not_required,
        hands_off=bool(config.get("enforcement", {}).get("hands_off", False)),
    )


# A floor reads at most this much of a message, so a pasted log cannot slow every prompt down.
FLOOR_MAX_CHARS = 20_000


def message_floor(message: str, config: Config) -> Floor | None:
    """The keyword assessment of the user's own message, kept as a minimum (see :class:`Floor`).

    Cheap: no discovery, no project context. A vague message is not treated as risky (no
    low-confidence penalty). ``None`` for an empty message.
    """
    try:
        a = without_vagueness(assess(message[:FLOOR_MAX_CHARS], config), config)
    except ValueError:
        return None
    return Floor(
        levels={name: d.level for name, d in a.dimensions.items()},
        signals=sorted(a.signal_ids()),
        areas=list(a.areas),
        profile=recommended_profile(a),
    )


def route_floor(record: dict[str, Any]) -> Floor | None:
    """A recorded route as a floor, for a route that raises or replaces it.

    Uses the levels before project context; records without them fall back to the stored
    dimension levels, which can only make the floor stricter.
    """
    base = Floor.from_dict(record.get("base"))
    if base is not None:
        return base
    return Floor.from_dict(
        {
            "levels": record.get("dimensions"),
            "signals": record.get("signals"),
            "areas": record.get("areas"),
            "profile": record.get("profile"),
        }
    )


def route_record(
    plan: TaskPlan, record_id: str, config: Config, session_id: str | None = None
) -> dict[str, Any]:
    """The persistent summary of a routing decision (no inventory details, no secrets)."""
    a = plan.assessment
    store_text = config.get("records", {}).get("store_task_text", True)
    return {
        "id": record_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "susanoo_version": __version__,
        "task": a.task if store_text else None,
        "task_sha256": hashlib.sha256(a.task.encode("utf-8")).hexdigest(),
        "profile": plan.decision.profile,
        "recommended": plan.decision.recommended,
        "overridden": plan.decision.overridden,
        "complexity": {"level": a.complexity.level, "score": a.complexity.score},
        "risk": {"level": a.risk.level, "score": a.risk.score},
        "uncertainty": {"level": a.uncertainty.level, "score": a.uncertainty.score},
        "confidence": a.confidence,
        "dimensions": {name: d.level for name, d in a.dimensions.items()},
        "signals": sorted(a.signal_ids()),
        "areas": a.areas,
        "controls": [c.id for c in plan.decision.controls],
        # Levels before project context: the floor for a route that raises or replaces this one.
        "base": {
            "levels": a.base_levels,
            "signals": sorted(a.signal_ids()),
            "areas": a.areas,
            "profile": plan.decision.profile,
        },
        "gaps": [{"capability": g.capability, "blocking": g.blocking} for g in plan.gaps],
        "verification": [{"id": s.id, "level": s.level, "mode": s.mode} for s in plan.verification.steps],
        "checkpoints": plan.decision.checkpoints,
        "phases": [p.title for p in plan.profile.phases],
        # Binds the route to the Claude Code session that created it (enforcement hooks).
        "session_id": session_id,
    }
