"""Tool and capability registry: packaged catalog plus optional project additions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from susanoo.config import ConfigError, load_toml_data, load_toml_file
from susanoo.paths import state_dir

TOOL_KINDS: tuple[str, ...] = ("native", "cli", "script", "skill", "mcp", "service")


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    title: str
    description: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    kind: str
    capabilities: tuple[str, ...]
    languages: tuple[str, ...] = ()
    binaries: tuple[str, ...] = ()
    python_packages: tuple[str, ...] = ()
    npm_packages: tuple[str, ...] = ()
    npm_scripts: tuple[str, ...] = ()
    make_targets: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    pyproject_tools: tuple[str, ...] = ()
    mcp_patterns: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ()
    run: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reliability: int = 2
    context_cost: int = 2
    setup_cost: int = 2
    security_exposure: int = 2
    install: str = ""
    notes: str = ""
    # "packaged" (shipped with Susanoo) or "project" (.susanoo/tools.toml). Project
    # entries are repository-controlled, so their `version_args` are never executed.
    origin: str = "packaged"


@dataclass
class Registry:
    tools: dict[str, ToolSpec]
    capabilities: dict[str, CapabilitySpec]

    def tools_for(self, capability: str) -> list[ToolSpec]:
        return [t for t in self.tools.values() if capability in t.capabilities]

    def capability_title(self, capability: str) -> str:
        spec = self.capabilities.get(capability)
        return spec.title if spec else capability


_TUPLE_FIELDS = (
    "capabilities",
    "languages",
    "binaries",
    "python_packages",
    "npm_packages",
    "npm_scripts",
    "make_targets",
    "files",
    "pyproject_tools",
    "mcp_patterns",
    "env",
    "version_args",
)
_INT_FIELDS = ("reliability", "context_cost", "setup_cost", "security_exposure")


def _tool_from_dict(entry: dict[str, Any], origin: str = "packaged") -> ToolSpec:
    tid = entry.get("id")
    if not isinstance(tid, str) or not tid:
        raise ConfigError("every [[tools]] entry needs a string 'id'")
    kind = entry.get("kind")
    if kind not in TOOL_KINDS:
        raise ConfigError(f"tool '{tid}': kind must be one of {TOOL_KINDS}")
    kwargs: dict[str, Any] = {"id": tid, "name": entry.get("name", tid), "kind": kind}
    for key in _TUPLE_FIELDS:
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"tool '{tid}': '{key}' must be a list of strings")
        kwargs[key] = tuple(value)
    if not kwargs["capabilities"]:
        raise ConfigError(f"tool '{tid}': 'capabilities' must not be empty")
    for key in _INT_FIELDS:
        value = entry.get(key, 2)
        if not isinstance(value, int) or not 1 <= value <= 3:
            raise ConfigError(f"tool '{tid}': '{key}' must be an integer from 1 to 3")
        kwargs[key] = value
    run = entry.get("run", {})
    if not isinstance(run, dict):
        raise ConfigError(f"tool '{tid}': 'run' must be a table of capability = [argv]")
    for capability, argv in run.items():
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ConfigError(f"tool '{tid}': run.{capability} must be a non-empty list of strings")
    kwargs["run"] = {cap: tuple(argv) for cap, argv in run.items()}
    kwargs["install"] = str(entry.get("install", ""))
    kwargs["notes"] = str(entry.get("notes", ""))
    kwargs["origin"] = origin
    if origin != "packaged":
        kwargs["version_args"] = ()
    return ToolSpec(**kwargs)


def load_registry(root: Path | None = None) -> Registry:
    """Load the packaged registry; ``.susanoo/tools.toml`` may add or replace tools by id."""
    tool_entries: dict[str, tuple[dict[str, Any], str]] = {
        t["id"]: (t, "packaged") for t in load_toml_data("tools.toml").get("tools", [])
    }
    if root is not None:
        extra = state_dir(root) / "tools.toml"
        if extra.is_file():
            entries = load_toml_file(extra).get("tools", [])
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                raise ConfigError(f"{extra}: [[tools]] must be an array of tables")
            for entry in entries:
                if entry.get("enabled") is False:
                    tool_entries.pop(entry.get("id", ""), None)
                else:
                    tool_entries[entry.get("id", "")] = (entry, "project")
    tools = {tid: _tool_from_dict(entry, origin) for tid, (entry, origin) in tool_entries.items()}

    capabilities: dict[str, CapabilitySpec] = {}
    for entry in load_toml_data("capabilities.toml").get("capabilities", []):
        capabilities[entry["id"]] = CapabilitySpec(
            id=entry["id"],
            title=entry.get("title", entry["id"]),
            description=entry.get("description", ""),
            keywords=tuple(entry.get("keywords", [])),
        )
    for tool in tools.values():
        for capability in tool.capabilities:
            if capability not in capabilities:
                raise ConfigError(f"tool '{tool.id}' references unknown capability '{capability}'")
    return Registry(tools=tools, capabilities=capabilities)
