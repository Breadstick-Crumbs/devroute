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
    # The tool is only detected when one of these project files or globs exists (screenshot
    # baselines for visual regression); the other detection keys still decide its status.
    requires_files: tuple[str, ...] = ()
    # The tool is only detected when one of these Python packages is installed in the environment
    # of its executable, or declared by the project when that environment cannot be inspected
    # (pytest-xdist for `pytest -n auto`).
    requires_python_packages: tuple[str, ...] = ()
    pyproject_tools: tuple[str, ...] = ()
    mcp_patterns: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ()
    # Only applies when the project uses one of these JavaScript package managers.
    package_managers: tuple[str, ...] = ()
    # Words in the task that make this tool the better pick ("postgres" -> psql).
    task_keywords: tuple[str, ...] = ()
    # Tools this one is a better form of: where it is detected, it ranks before them and is
    # never suggested where it is not (parallel pytest over pytest).
    supersedes: tuple[str, ...] = ()
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


@dataclass(frozen=True)
class EnvironmentSpec:
    """A project environment manager whose virtualenv lives outside the project by default."""

    id: str
    name: str
    binary: str
    run_prefix: tuple[str, ...]
    markers: tuple[str, ...] = ()
    pyproject_tools: tuple[str, ...] = ()
    # Files that mean another manager owns the environment (e.g. uv.lock after a migration).
    unless_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageManagerSpec:
    """A JavaScript package manager: how to recognize it and how its commands are spelled."""

    id: str
    markers: tuple[str, ...]
    add_dev: str
    run: tuple[str, ...]
    create: str = ""


@dataclass
class Registry:
    tools: dict[str, ToolSpec]
    capabilities: dict[str, CapabilitySpec]
    environments: tuple[EnvironmentSpec, ...] = ()
    package_managers: dict[str, PackageManagerSpec] = field(default_factory=dict)

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
    "requires_files",
    "requires_python_packages",
    "pyproject_tools",
    "mcp_patterns",
    "env",
    "version_args",
    "package_managers",
    "task_keywords",
    "supersedes",
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


def _environment_from_dict(entry: dict[str, Any]) -> EnvironmentSpec:
    eid = entry.get("id")
    if not isinstance(eid, str) or not eid:
        raise ConfigError("every [[environments]] entry needs a string 'id'")
    binary = entry.get("binary")
    if not isinstance(binary, str) or not binary:
        raise ConfigError(f"environment '{eid}': 'binary' must be a non-empty string")
    lists: dict[str, tuple[str, ...]] = {}
    for key in ("run_prefix", "markers", "pyproject_tools", "unless_markers"):
        value = entry.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"environment '{eid}': '{key}' must be a list of strings")
        lists[key] = tuple(value)
    if not lists["run_prefix"]:
        raise ConfigError(f"environment '{eid}': 'run_prefix' must not be empty")
    if not lists["markers"] and not lists["pyproject_tools"]:
        raise ConfigError(f"environment '{eid}': needs 'markers' or 'pyproject_tools'")
    return EnvironmentSpec(id=eid, name=str(entry.get("name", eid)), binary=binary, **lists)


def _package_manager_from_dict(entry: dict[str, Any]) -> PackageManagerSpec:
    pid = entry.get("id")
    markers, run, add_dev = entry.get("markers"), entry.get("run"), entry.get("add_dev")
    if not isinstance(pid, str) or not pid:
        raise ConfigError("every [[package_managers]] entry needs a string 'id'")
    if not isinstance(markers, list) or not all(isinstance(m, str) for m in markers):
        raise ConfigError(f"package manager '{pid}': 'markers' must be a list of strings")
    if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
        raise ConfigError(f"package manager '{pid}': 'run' must be a non-empty list of strings")
    if not isinstance(add_dev, str) or not add_dev:
        raise ConfigError(f"package manager '{pid}': 'add_dev' must be a non-empty string")
    create = entry.get("create", "")
    if not isinstance(create, str):
        raise ConfigError(f"package manager '{pid}': 'create' must be a string")
    return PackageManagerSpec(id=pid, markers=tuple(markers), add_dev=add_dev, run=tuple(run), create=create)


def load_registry(root: Path | None = None) -> Registry:
    """Load the packaged registry; ``.susanoo/tools.toml`` may add or replace tools by id."""
    packaged = load_toml_data("tools.toml")
    tool_entries: dict[str, tuple[dict[str, Any], str]] = {
        t["id"]: (t, "packaged") for t in packaged.get("tools", [])
    }
    # Only the packaged catalog defines package managers: their commands end up in `verify`.
    package_managers = {
        pm.id: pm for pm in (_package_manager_from_dict(e) for e in packaged.get("package_managers", []))
    }
    # Packaged only: a run prefix decides what `verify --execute` runs, so a repository
    # cannot supply its own.
    environments = tuple(_environment_from_dict(e) for e in packaged.get("environments", []))
    disabled: set[str] = set()
    if root is not None:
        extra = state_dir(root) / "tools.toml"
        if extra.is_file():
            entries = load_toml_file(extra).get("tools", [])
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                raise ConfigError(f"{extra}: [[tools]] must be an array of tables")
            for entry in entries:
                if entry.get("enabled") is False:
                    tool_entries.pop(entry.get("id", ""), None)
                    disabled.add(str(entry.get("id", "")))
                else:
                    tool_entries[entry.get("id", "")] = (entry, "project")
    tools = {tid: _tool_from_dict(entry, origin) for tid, (entry, origin) in tool_entries.items()}
    dropped: set[str] = set()
    for tool in tools.values():
        for other in tool.supersedes:
            if other in disabled:
                dropped.add(tool.id)  # A better form of a tool the project turned off is off too.
            elif other not in tools:
                raise ConfigError(f"tool '{tool.id}' supersedes unknown tool '{other}'")
            elif tool.origin == "packaged" and tools[other].origin != "packaged":
                dropped.add(tool.id)  # The project's own command for that tool wins.
    tools = {tid: tool for tid, tool in tools.items() if tid not in dropped}

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
    return Registry(
        tools=tools,
        capabilities=capabilities,
        environments=environments,
        package_managers=package_managers,
    )
