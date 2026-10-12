"""End-to-end planning: assessment -> route -> capabilities -> tools -> gaps -> verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devroute import __version__
from devroute.analyzer import Assessment, CapabilityRequirement, ProjectContext, assess
from devroute.config import Config
from devroute.discovery import Inventory, build_inventory, has_test_framework
from devroute.registry import Registry, load_registry
from devroute.research import Gap, analyze_gaps
from devroute.router import Profile, RouteDecision, load_profiles, route
from devroute.selection import ToolRecommendation, recommend
from devroute.verification import VerificationPlan, build_plan, select_steps, verification_requirements


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

    def to_dict(self, include_inventory: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "devroute_version": __version__,
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
) -> TaskPlan:
    registry = registry or load_registry(root)
    profiles = profiles or load_profiles(root)
    inventory = inventory or build_inventory(root, registry, verify=verify_tools, home=home)

    assessment = assess(task, config, project_context(inventory))
    decision = route(assessment, profiles, config, override=override)
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
    recommendations = recommend(requirements, inventory, registry)
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
    )


def route_record(plan: TaskPlan, record_id: str, config: Config) -> dict[str, Any]:
    """The persistent summary of a routing decision (no inventory details, no secrets)."""
    a = plan.assessment
    store_text = config.get("records", {}).get("store_task_text", True)
    return {
        "id": record_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "devroute_version": __version__,
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
        "gaps": [{"capability": g.capability, "blocking": g.blocking} for g in plan.gaps],
        "verification": [{"id": s.id, "level": s.level, "mode": s.mode} for s in plan.verification.steps],
    }
