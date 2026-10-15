"""Tool Selection Engine: pick the simplest reliable tool for each required capability.

Availability dominates the ranking (a working tool beats a better one that is not
installed). Among comparable options, native Claude Code tools, CLIs, and project
scripts are preferred over MCP servers and external services because they add
less context, less setup, and less standing access.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from susanoo.analyzer.models import CapabilityRequirement
from susanoo.discovery.models import Finding, Inventory, Status
from susanoo.registry import PackageManagerSpec, Registry, ToolSpec

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
    adjustments: list[str] = field(default_factory=list)
    # Why a tool that has a run command cannot be run by Susanoo here (the check becomes manual).
    command_withheld: str = ""

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
    extensions: list[Candidate] = field(default_factory=list)

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
            "extensions": [c.to_dict() for c in self.extensions],
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


# Used when the registry defines no package managers (e.g. a test registry).
_NPM = PackageManagerSpec(
    id="npm",
    markers=("package-lock.json",),
    add_dev="npm install --save-dev",
    run=("npm", "run"),
    create="npm init",
)


def package_manager(
    inventory: Inventory, registry: Registry, context: SelectionContext | None = None
) -> PackageManagerSpec:
    """The project's JavaScript package manager, else the configured default (only with a
    ``context``), else npm."""
    default = context.default_package_manager if context is not None else None
    for pid in (inventory.package_manager, default, "npm"):
        if pid and pid in registry.package_managers:
            return registry.package_managers[pid]
    return _NPM


def _expand(argv: tuple[str, ...], pm: PackageManagerSpec) -> list[str]:
    """``{js_run}`` becomes the package manager's script runner (``bun test`` is Bun's own test
    runner, so scripts always go through ``run``)."""
    out: list[str] = []
    for arg in argv:
        out += list(pm.run) if arg == "{js_run}" else [arg]
    return out


def _registry_candidate(
    tool: ToolSpec, capability: str, inventory: Inventory, pm: PackageManagerSpec, run_pm: PackageManagerSpec
) -> Candidate | None:
    if tool.package_managers and inventory.package_manager not in tool.package_managers:
        return None  # e.g. `npm audit` needs package-lock.json; a pnpm project audits with pnpm.
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
    # Discovery picks the project-configured variant of `run` (see ToolSpec.configured_run).
    configured = finding.detail.get("run") if finding else None
    run = configured if isinstance(configured, dict) else tool.run
    command = _expand(tuple(run[capability]), run_pm) if capability in run else None
    evidence = list(finding.evidence) if finding else []
    withheld = ""
    environment = next(iter(inventory.by_kind("environment")), None)
    if command and environment is not None and tool.kind in ("cli", "script") and "python" in tool.languages:
        command, withheld = _through_environment(command, environment, evidence)
        declared = finding is not None and finding.detail.get("python_declared")
        if command and declared and status is Status.CONFIGURED_INACCESSIBLE:
            # Installed into the external environment, which PATH lookups cannot see.
            status = Status.INSTALLED_UNVERIFIED
            evidence.append(f"expected in the {environment.name} environment (declared dependency)")
    return Candidate(
        id=tool.id,
        name=tool.name,
        kind=tool.kind,
        status=status,
        score=_score(
            status, tool.kind, tool.reliability, tool.context_cost, tool.setup_cost, tool.security_exposure
        ),
        evidence=evidence,
        command=command,
        install=tool.install.replace("{js_add_dev}", pm.add_dev).replace(
            "{js_create}", pm.create or "npm init"
        ),
        notes=tool.notes,
        command_withheld=withheld,
    )


def _through_environment(
    command: list[str], environment: Finding, evidence: list[str]
) -> tuple[list[str] | None, str]:
    """Run a Python tool inside the project's external environment, or withhold the command.

    Running it from PATH instead would use another interpreter without the project's
    dependencies, and its result would be recorded as executed evidence.
    """
    prefix = [str(part) for part in environment.detail.get("run_prefix", [])]
    if environment.status.usable and prefix:
        evidence.append(f"runs in the {environment.name} environment (`{' '.join(prefix)}`)")
        return prefix + command, ""
    binary = environment.detail.get("binary", environment.name)
    return None, (
        f"{environment.name} project without an in-project environment and '{binary}' is not on PATH; "
        "run this check in the project's environment and record the result with `susanoo evidence`"
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


@dataclass(frozen=True)
class SelectionContext:
    """Project facts that change which tool is the right one, not just which is available."""

    large_repo_files: int = 2000
    size_adjustment: float = 5.0
    default_package_manager: str = "npm"
    task: str = ""
    task_keyword_bonus: float = 3.0

    @classmethod
    def from_config(cls, config: dict[str, Any] | None, task: str = "") -> SelectionContext:
        if not config:
            return cls(task=task)
        selection = config.get("selection", {})
        return cls(
            large_repo_files=int(config.get("context", {}).get("large_repo_files", 2000)),
            size_adjustment=float(selection.get("code_navigation_size_adjustment", 5.0)),
            default_package_manager=str(selection.get("default_package_manager", "npm")),
            task=task,
            task_keyword_bonus=float(selection.get("task_keyword_bonus", 3.0)),
        )


def _adjust_for_task(candidates: list[Candidate], registry: Registry, context: SelectionContext) -> None:
    """A tool the task names (or whose technology it names) beats an equally available one.
    The bonus is smaller than the gap between installed and missing, so it never makes a missing
    tool win over a working one."""
    task = context.task.lower()
    for c in candidates:
        spec = registry.tools.get(c.id)
        if spec is None:
            continue
        hit = next(
            (
                k
                for k in spec.task_keywords
                if re.search(r"(?<![\w-])" + re.escape(k.lower()) + r"(?![\w-])", task)
            ),
            None,
        )
        if hit:
            c.score += context.task_keyword_bonus
            c.adjustments.append(f"the task mentions '{hit}'")


# Kinds that index the codebase (graphs, language servers) rather than scanning text.
_INDEXED_KINDS = frozenset({"mcp", "skill", "agent"})


def _adjust_for_size(candidates: list[Candidate], inventory: Inventory, context: SelectionContext) -> None:
    """Code navigation depends on repository size: text search is enough for small repositories,
    while an index pays for its context cost in large ones."""
    files = inventory.source_files
    large = files >= context.large_repo_files
    for c in candidates:
        if c.kind in _INDEXED_KINDS:
            delta = context.size_adjustment if large else -context.size_adjustment
            reason = (
                f"large repository ({files} source files): an index saves search-and-read round trips"
                if large
                else f"small repository ({files} source files): built-in search is sufficient"
            )
        elif c.kind == "native" and large:
            delta = -context.size_adjustment
            reason = (
                f"large repository ({files} source files): text search scales poorly for call-graph questions"
            )
        else:
            continue
        c.score += delta
        c.adjustments.append(reason)


def candidates_for(
    capability: str,
    inventory: Inventory,
    registry: Registry,
    context: SelectionContext | None = None,
) -> list[Candidate]:
    context = context or SelectionContext()
    # Install hints may follow the configured default; run commands use the manager discovery
    # checked for (npm without a lockfile), so a "ready" check never runs a missing binary.
    pm = package_manager(inventory, registry, context)
    run_pm = package_manager(inventory, registry)
    candidates: list[Candidate] = []
    for tool in registry.tools_for(capability):
        candidate = _registry_candidate(tool, capability, inventory, pm, run_pm)
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
    if capability == "code_navigation":
        _adjust_for_size(candidates, inventory, context)
    if context.task:
        _adjust_for_task(candidates, registry, context)
    # A tool whose command had to be withheld ranks after runnable ones (e.g. `make test`).
    return sorted(candidates, key=lambda c: (bool(c.command_withheld), -c.score, c.name))


# Kinds a user adds to Claude Code on purpose: worth naming even when a simpler tool is selected.
EXTENSION_KINDS = frozenset({"skill", "agent", "mcp"})


def installed_extensions(rec: ToolRecommendation, limit: int = 3) -> list[Candidate]:
    """Usable skills, subagents, and MCP servers for this capability besides the selected tool."""
    return rec.extensions[:limit]


def _extensions(candidates: list[Candidate], selected: Candidate | None) -> list[Candidate]:
    """Installed extensions, best of each kind first, so three similar subagents do not hide a
    skill or an MCP server."""
    usable = [c for c in candidates if c is not selected and c.status.usable and c.kind in EXTENSION_KINDS]
    firsts: list[Candidate] = []
    for c in usable:
        if all(f.kind != c.kind for f in firsts):
            firsts.append(c)
    return firsts + [c for c in usable if c not in firsts]


def _rationale(selected: Candidate | None, suggestion: Candidate | None, others: list[Candidate]) -> str:
    if selected is not None:
        parts = [f"{selected.name} ({selected.kind}) is {selected.status.value.replace('_', ' ')}"]
        parts += selected.adjustments
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
    return "No usable tool found and no known candidate in the registry; see `susanoo research`."


def recommend(
    requirements: list[CapabilityRequirement],
    inventory: Inventory,
    registry: Registry,
    context: SelectionContext | None = None,
) -> list[ToolRecommendation]:
    recommendations: list[ToolRecommendation] = []
    for req in requirements:
        candidates = candidates_for(req.capability, inventory, registry, context)
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
                extensions=_extensions(candidates, selected),
            )
        )
    return recommendations
