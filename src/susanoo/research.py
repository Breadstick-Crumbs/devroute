"""Capability Research Engine: identify gaps and evaluate ways to close them.

v0.1 ships one provider, ``LocalRegistryProvider``, which evaluates the packaged
tool registry against the local inventory. External discovery (package indexes,
MCP registries, the web) plugs in through the ``ResearchProvider`` protocol but is
not implemented yet. Within Claude Code, the ``susanoo-researcher`` subagent can
perform manual web research and report back. Nothing in this module installs
software or edits configuration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from susanoo.discovery.models import Inventory, Status
from susanoo.registry import Registry
from susanoo.selection import Candidate, SelectionContext, ToolRecommendation, candidates_for

KIND_SECURITY_NOTES: dict[str, str] = {
    "native": "Built into Claude Code and governed by its permission settings.",
    "cli": "Install from the official distribution or a trusted package manager, and pin the version.",
    "script": "Runs commands defined in the project; review the script before running it.",
    "skill": "Skills and plugins can direct the agent and may bundle scripts or hooks; read them before enabling.",
    "agent": "Subagent definitions control tools and permissions; review the tools they are granted.",
    "mcp": (
        "MCP servers run with your user permissions and their tool definitions add context to every session; "
        "review the publisher and source, and give them least-privilege credentials."
    ),
    "service": "External services may receive code or data; check data handling terms before use.",
}


@dataclass
class Gap:
    capability: str
    title: str
    level: str
    status: Status
    blocking: bool
    reasons: list[str]
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def analyze_gaps(recommendations: list[ToolRecommendation]) -> list[Gap]:
    """A gap is a requirement with no usable tool. Required gaps block the relevant phase."""
    gaps: list[Gap] = []
    for rec in recommendations:
        if not rec.gap:
            continue
        if rec.suggestion is not None:
            verb = (
                "fix the configuration of"
                if rec.suggestion.status is Status.CONFIGURED_INACCESSIBLE
                else "consider"
            )
            suggestion = f"{verb} {rec.suggestion.name} ({rec.suggestion.kind})"
            if rec.suggestion.install and rec.suggestion.status is not Status.CONFIGURED_INACCESSIBLE:
                suggestion += f", install: {rec.suggestion.install}"
        else:
            suggestion = f"run `susanoo research {rec.capability}`"
        gaps.append(
            Gap(
                capability=rec.capability,
                title=rec.title,
                level=rec.level,
                status=rec.status,
                blocking=rec.level == "required",
                reasons=rec.reasons,
                suggestion=suggestion,
            )
        )
    return gaps


@dataclass
class ResearchCandidate:
    id: str
    name: str
    kind: str
    status: Status
    provider: str
    install: str = ""
    security_notes: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ResearchProvider(Protocol):
    """A source of candidate tools for a capability."""

    name: str

    def search(self, capability: str) -> list[ResearchCandidate]: ...


class LocalRegistryProvider:
    """Evaluates the packaged registry plus locally discovered skills, agents, and MCP servers."""

    name = "local-registry"

    def __init__(
        self, inventory: Inventory, registry: Registry, context: SelectionContext | None = None
    ) -> None:
        self.inventory = inventory
        self.registry = registry
        self.context = context

    def search(self, capability: str) -> list[ResearchCandidate]:
        found = candidates_for(capability, self.inventory, self.registry, self.context)
        return [self._convert(c) for c in found]

    def _convert(self, candidate: Candidate) -> ResearchCandidate:
        notes = [KIND_SECURITY_NOTES.get(candidate.kind, "")]
        tool = self.registry.tools.get(candidate.id)
        if tool is not None and tool.security_exposure >= 3:
            notes.append(
                "Grants broad access (accounts, data, or infrastructure); use disposable or test-mode credentials."
            )
        return ResearchCandidate(
            id=candidate.id,
            name=candidate.name,
            kind=candidate.kind,
            status=candidate.status,
            provider=self.name,
            install=candidate.install,
            security_notes=[n for n in notes if n],
            notes=candidate.notes,
        )


@dataclass
class ResearchReport:
    capability: str
    title: str
    created_at: str
    decision: str  # use-existing | fix-configuration | adopt | custom-or-research
    recommendation: str
    candidates: list[ResearchCandidate]
    validation_steps: list[str]
    providers: list[str]
    limitations: list[str]
    task: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [c.to_dict() for c in self.candidates]
        return data


def research(
    capability: str,
    inventory: Inventory,
    registry: Registry,
    providers: list[ResearchProvider] | None = None,
    task: str | None = None,
    context: SelectionContext | None = None,
) -> ResearchReport:
    if capability not in registry.capabilities:
        known = ", ".join(sorted(registry.capabilities))
        raise ValueError(f"unknown capability '{capability}'. Known capabilities: {known}")
    sources: list[ResearchProvider] = providers or [LocalRegistryProvider(inventory, registry, context)]
    candidates: list[ResearchCandidate] = []
    for provider in sources:
        candidates += provider.search(capability)

    usable = [c for c in candidates if c.status.usable]
    misconfigured = [c for c in candidates if c.status is Status.CONFIGURED_INACCESSIBLE]
    missing = [c for c in candidates if c.status is Status.MISSING]

    if usable:
        decision = "use-existing"
        recommendation = f"Use {usable[0].name}: it is already {usable[0].status.value.replace('_', ' ')}."
    elif misconfigured:
        decision = "fix-configuration"
        recommendation = (
            f"{misconfigured[0].name} is configured but not usable here. Install or enable it "
            "(for example, install declared dependencies) after reviewing the change."
        )
    elif missing:
        best = missing[0]
        hint = f" Install: {best.install}" if best.install else ""
        decision = "adopt"
        recommendation = f"Adopt {best.name} ({best.kind}) with your approval.{hint}"
    else:
        decision = "custom-or-research"
        recommendation = (
            "No known candidate. Options: write a small project script, research externally with the "
            "susanoo-researcher subagent in Claude Code, or accept the gap and verify manually."
        )

    validation = [
        "Review the security notes and the tool's publisher before installing anything.",
        "Install into a disposable or project-local environment (virtualenv, devDependencies), not globally.",
        "Run `susanoo inventory --verify` and confirm the capability shows as available.",
        "Run the tool once against the project and record the result (`susanoo verify --execute` or `susanoo evidence`).",
    ]
    if decision == "use-existing":
        validation = validation[2:]

    return ResearchReport(
        capability=capability,
        title=registry.capability_title(capability),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        decision=decision,
        recommendation=recommendation,
        candidates=candidates,
        validation_steps=validation,
        providers=[p.name for p in sources],
        limitations=[
            "Only the packaged registry and locally discovered tools were evaluated; no internet search was performed.",
            "Statuses come from read-only detection unless `--verify` was used.",
        ],
        task=task,
    )
