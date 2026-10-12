"""Tool Selection Engine: pick the simplest reliable tool for each required capability.

Availability dominates the ranking (a working tool beats a better one that is not
installed). Among comparable options, native Claude Code tools, CLIs, and project
scripts are preferred over MCP servers and external services because they add
less context, less setup, and less standing access.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from devroute.analyzer.models import CapabilityRequirement
from devroute.discovery.models import Finding, Inventory, Status
from devroute.registry import Registry, ToolSpec

KIND_PREFERENCE: dict[str, float] = {
    "native": 3,
    "cli": 3,
    "script": 3,
    "skill": 2,
    "agent": 2,
    "mcp": 1,
    "service": 1,
}
AVAILABILITY_SCORE: dict[Status, float] = {
    Status.AVAILABLE_VERIFIED: 10,
    Status.INSTALLED_UNVERIFIED: 8,
    Status.CONFIGURED_INACCESSIBLE: 3,
    Status.MISSING: 0,
    Status.NOT_REQUIRED: 0,
}
# Defaults for things discovered in Claude Code configuration (not in the registry).
_DISCOVERED_PROFILE: dict[str, tuple[str, int, int, int, int]] = {
    # finding kind: (tool kind, reliability, context_cost, setup_cost, security_exposure)
    "skill": ("skill", 2, 2, 1, 1),
    "command": ("skill", 2, 1, 1, 1),
    "agent": ("agent", 2, 1, 1, 1),
    "plugin": ("skill", 2, 2, 1, 2),
    "mcp_server": ("mcp", 2, 3, 2, 2),
}


@dataclass
class Candidate:
    id: str
    name: str
    kind: str
    status: Status
    score: float
    evidence: list[str] = field(default_factory=list)
    command: list[str] | None = None
    install: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ToolRecommendation:
    capability: str
    title: str
    level: str
    reasons: list[str]
    status: Status
    selected: Candidate | None
    suggestion: Candidate | None
    alternatives: list[Candidate]
    rationale: str

    @property
    def gap(self) -> bool:
        return self.selected is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "title": self.title,
            "level": self.level,
            "reasons": self.reasons,
            "status": self.status.value,
            "gap": self.gap,
            "selected": self.selected.to_dict() if self.selected else None,
            "suggestion": self.suggestion.to_dict() if self.suggestion else None,
            "alternatives": [c.to_dict() for c in self.alternatives],
            "rationale": self.rationale,
        }


def _score(status: Status, kind: str, reliability: int, context: int, setup: int, exposure: int) -> float:
    return (
        AVAILABILITY_SCORE[status]
        + KIND_PREFERENCE.get(kind, 1)
        + reliability
        - context
        - 0.5 * setup
        - 0.5 * exposure
    )


def _registry_candidate(tool: ToolSpec, capability: str, inventory: Inventory) -> Candidate | None:
    finding = inventory.get(f"tool:{tool.id}")
    status = finding.status if finding else Status.MISSING
    configured_mcp = any(f.kind == "mcp_server" and capability in f.capabilities for f in inventory.findings)
    if tool.kind == "mcp" and finding is None and configured_mcp:
        return None  # A configured MCP server already represents this option.
    # Language-specific tools only count for projects that use that language,
    # unless the project itself configures them.
    language_mismatch = bool(tool.languages) and not set(tool.languages) & set(inventory.languages)
    if language_mismatch and (finding is None or finding.scope != "project"):
        return None
    if tool.kind == "script" and finding is None:
        return None  # Project scripts are only meaningful where they exist.
    command = list(tool.run[capability]) if capability in tool.run else None
    return Candidate(
        id=tool.id,
        name=tool.name,
        kind=tool.kind,
        status=status,
        score=_score(
            status, tool.kind, tool.reliability, tool.context_cost, tool.setup_cost, tool.security_exposure
        ),
        evidence=list(finding.evidence) if finding else [],
        command=command,
        install=tool.install,
        notes=tool.notes,
    )


def _discovered_candidate(finding: Finding) -> Candidate:
    kind, reliability, context, setup, exposure = _DISCOVERED_PROFILE[finding.kind]
    label = {"agent": "subagent", "mcp_server": "MCP server", "command": "slash command"}.get(
        finding.kind, finding.kind
    )
    return Candidate(
        id=finding.id,
        name=f"{finding.name} ({label}, {finding.scope})",
        kind=kind,
        status=finding.status,
        score=_score(finding.status, kind, reliability, context, setup, exposure),
        evidence=list(finding.evidence),
        notes=str(finding.detail.get("description", ""))[:160],
    )


def candidates_for(capability: str, inventory: Inventory, registry: Registry) -> list[Candidate]:
    candidates: list[Candidate] = []
    for tool in registry.tools_for(capability):
        candidate = _registry_candidate(tool, capability, inventory)
        if candidate is not None:
            candidates.append(candidate)
    for finding in inventory.findings:
        if finding.kind in _DISCOVERED_PROFILE and capability in finding.capabilities:
            candidates.append(_discovered_candidate(finding))
    vcs = inventory.get("vcs:git-repository")
    if capability == "version_control" and vcs is None:
        for candidate in candidates:
            if candidate.id == "git" and candidate.status.usable:
                candidate.notes = "git is installed but this project is not a repository yet"
    return sorted(candidates, key=lambda c: (-c.score, c.name))


def _rationale(selected: Candidate | None, suggestion: Candidate | None, others: list[Candidate]) -> str:
    if selected is not None:
        parts = [f"{selected.name} ({selected.kind}) is {selected.status.value.replace('_', ' ')}"]
        heavier = [
            c for c in others if KIND_PREFERENCE.get(c.kind, 1) < KIND_PREFERENCE.get(selected.kind, 1)
        ]
        if heavier:
            parts.append(
                f"preferred over {heavier[0].name} ({heavier[0].kind}): lower context overhead and setup"
            )
        return "; ".join(parts) + "."
    if suggestion is not None:
        state = suggestion.status.value.replace("_", " ")
        return (
            f"No usable tool found. Best option: {suggestion.name} ({suggestion.kind}, {state}); "
            "adopting it needs your approval."
        )
    return "No usable tool found and no known candidate in the registry; see `devroute research`."


def recommend(
    requirements: list[CapabilityRequirement], inventory: Inventory, registry: Registry
) -> list[ToolRecommendation]:
    recommendations: list[ToolRecommendation] = []
    for req in requirements:
        candidates = candidates_for(req.capability, inventory, registry)
        usable = [c for c in candidates if c.status.usable]
        selected = usable[0] if usable else None
        suggestion = None if selected else (candidates[0] if candidates else None)
        rest = [c for c in candidates if c is not selected and c is not suggestion]
        status = (
            selected.status
            if selected
            else (
                suggestion.status
                if suggestion and suggestion.status is Status.CONFIGURED_INACCESSIBLE
                else Status.MISSING
            )
        )
        recommendations.append(
            ToolRecommendation(
                capability=req.capability,
                title=registry.capability_title(req.capability),
                level=req.level,
                reasons=list(dict.fromkeys(req.reasons)),
                status=status,
                selected=selected,
                suggestion=suggestion,
                alternatives=rest[:3],
                rationale=_rationale(selected, suggestion, rest),
            )
        )
    return recommendations
