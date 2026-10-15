"""Capability Intelligence Engine: build a structured inventory of the environment."""

from __future__ import annotations

import os

# Bandit B404: only used for opt-in `--version` probes of registry tools.
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from susanoo.architecture import detect_architecture_files
from susanoo.discovery.executables import (
    PROJECT_ENV_DIRS,
    describe_location,
    has_project_python_env,
    python_package_installed,
    resolve_executable,
)
from susanoo.discovery.models import Finding, Inventory, Status
from susanoo.discovery.project import (
    BUILD_FILES,
    ProjectFacts,
    is_github_remote,
    normalize_package,
    scan_project,
)
from susanoo.registry import Registry, ToolSpec
from susanoo.ui import detect_design_files, detect_domains

VERSION_TIMEOUT_SECONDS = 5


def _probe_version(executable: str, args: tuple[str, ...]) -> tuple[bool, str]:
    """Run ``<executable> <args>`` with a timeout and no shell. Used only with ``--verify``."""
    try:
        # Bandit B603: argv list from the packaged registry, no shell.
        proc = subprocess.run(  # nosec B603
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"version check failed: {exc}"
    output = (proc.stdout or proc.stderr).strip().splitlines()
    first = output[0][:120] if output else ""
    if proc.returncode == 0:
        return True, f"version check passed: {first}" if first else "version check passed"
    return False, f"version check exited with code {proc.returncode}"


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def detect_tool(
    tool: ToolSpec,
    facts: ProjectFacts,
    env: Mapping[str, str],
    verify: bool,
    which: Callable[[str], str | None] | None = None,
) -> Finding | None:
    """Detect one registry tool without executing it (unless ``verify`` is set)."""
    if tool.kind == "mcp":
        return None  # MCP servers are discovered from Claude Code configuration instead.
    required_files = [f for f in tool.requires_files if facts.has_file(f)]
    if tool.requires_files and not required_files:
        return None

    def lookup(name: str) -> str | None:
        return which(name) if which is not None else resolve_executable(name, facts.root)

    evidence: list[str] = [f"found: {', '.join(required_files[:3])}"] if required_files else []
    if tool.requires_python_packages:
        found_package = _required_python_package(tool, facts, lookup)
        if found_package is None:
            return None
        evidence.append(found_package)
    runnable = False
    configured = False
    verified = False
    found_binary: str | None = None

    for var in tool.env:
        if env.get(var):
            verified = True
            evidence.append(f"running inside a Claude Code session ({var} is set)")
    for binary in tool.binaries:
        path = lookup(binary)
        if path:
            runnable = True
            found_binary = found_binary or str(path)
            evidence.append(f"'{binary}' found {describe_location(str(path), facts.root)}")
            break
    declared_py = [p for p in tool.python_packages if normalize_package(p) in facts.python_packages]
    if declared_py:
        configured = True
        evidence.append(f"declared in Python dependencies: {', '.join(declared_py)}")
        if not tool.binaries:
            runnable = True
            evidence.append("no executable to check; installation not verified")
    declared_npm = [p for p in tool.npm_packages if p in facts.npm_packages]
    if declared_npm:
        configured = True
        if any(facts.npm_installed(p) for p in declared_npm):
            runnable = True
            evidence.append(f"npm package installed: {', '.join(declared_npm)}")
        else:
            evidence.append(f"declared in package.json but node_modules missing: {', '.join(declared_npm)}")
    scripts = [s for s in tool.npm_scripts if s in facts.npm_scripts]
    if scripts:
        configured = True
        evidence.append(f"package.json script(s): {', '.join(scripts)}")
        if lookup(facts.package_manager or "npm"):
            runnable = True
    targets = [t for t in tool.make_targets if t in facts.make_targets]
    if targets:
        configured = True
        evidence.append(f"Makefile target(s): {', '.join(targets)}")
        if lookup("make"):
            runnable = True
    sections = [s for s in tool.pyproject_tools if s in facts.pyproject_tools]
    if sections:
        configured = True
        evidence.append(f"configured in pyproject.toml: {', '.join('[tool.' + s + ']' for s in sections)}")
    files = [f for f in tool.files if facts.has_file(f)]
    if files:
        configured = True
        evidence.append(f"config file(s) present: {', '.join(files[:3])}")
        if tool.kind == "service":
            runnable = True

    if not (runnable or configured or verified):
        return None
    mismatch = bool(tool.languages and facts.languages) and not set(tool.languages) & set(facts.languages)
    if mismatch and not configured:
        evidence.append("project language does not match this tool; listed for completeness")

    if verified:
        status = Status.AVAILABLE_VERIFIED
    elif runnable:
        status = Status.INSTALLED_UNVERIFIED
        if verify and found_binary and tool.version_args:
            if _inside(found_binary, facts.root):
                # Executables shipped inside the repository are repository-controlled code.
                evidence.append("version check skipped: executable lives inside the project")
            else:
                ok, message = _probe_version(found_binary, tool.version_args)
                evidence.append(message)
                if ok:
                    status = Status.AVAILABLE_VERIFIED
    else:
        status = Status.CONFIGURED_INACCESSIBLE

    return Finding(
        id=f"tool:{tool.id}",
        kind="tool",
        name=tool.name,
        status=status,
        capabilities=list(tool.capabilities),
        evidence=evidence,
        scope="project" if configured else "system",
        tool_kind=tool.kind,
        detail={"registry_id": tool.id, "python_declared": bool(declared_py)},
    )


def _required_python_package(
    tool: ToolSpec, facts: ProjectFacts, lookup: Callable[[str], str | None]
) -> str | None:
    """Evidence that one of ``tool.requires_python_packages`` is where the tool runs, or ``None``.

    The project's own environment decides by what is installed in it. A tool found elsewhere (on
    PATH) may not be the one the checks run with, so the project must also declare the package;
    when no executable is found (Poetry and uv keep environments outside the project), the
    declaration decides.
    """
    executable = next((path for name in tool.binaries if (path := lookup(name))), None)
    declared = [p for p in tool.requires_python_packages if normalize_package(p) in facts.python_packages]
    if executable is None:
        return f"declared in Python dependencies: {', '.join(declared)}" if declared else None
    in_project = _inside(executable, facts.root)
    if not (in_project or declared):
        return None
    where = describe_location(executable, facts.root)
    for package in declared or tool.requires_python_packages:
        installed = python_package_installed(package, executable)
        if installed:
            return f"{package} installed {where}"
        if installed is None and declared:
            return f"declared in Python dependencies: {', '.join(declared)}"
    return None


def detect_environment(
    registry: Registry, facts: ProjectFacts, lookup: Callable[[str], str | None]
) -> Finding | None:
    """The environment manager that owns this project's virtualenv, when it lives outside the project.

    Without one, project tools resolve through the project's own .venv or PATH as usual.
    """
    if has_project_python_env(facts.root):
        return None
    for spec in registry.environments:
        if any(facts.has_file(m) for m in spec.unless_markers):
            continue
        evidence = [f"{m} present" for m in spec.markers if facts.has_file(m)]
        evidence += [
            f"[tool.{s}] in pyproject.toml" for s in spec.pyproject_tools if s in facts.pyproject_tools
        ]
        if not evidence:
            continue
        evidence.append("no in-project environment (" + ", ".join(PROJECT_ENV_DIRS) + ")")
        path = lookup(spec.binary)
        if path:
            evidence.append(f"'{spec.binary}' found {describe_location(path, facts.root)}")
            status = Status.INSTALLED_UNVERIFIED
        else:
            evidence.append(f"'{spec.binary}' not found on PATH")
            status = Status.CONFIGURED_INACCESSIBLE
        return Finding(
            id=f"environment:{spec.id}",
            kind="environment",
            name=spec.name,
            status=status,
            evidence=evidence,
            detail={"binary": spec.binary, "run_prefix": list(spec.run_prefix)},
        )
    return None


def detect_package_manager(facts: ProjectFacts, registry: Registry) -> str | None:
    """The project's JavaScript package manager: package.json "packageManager" first, then the
    first lockfile found in registry order. ``None`` when there is no package.json or no hint."""
    if not facts.has_file("package.json"):
        return None
    if facts.declared_package_manager in registry.package_managers:
        return facts.declared_package_manager
    for pm in registry.package_managers.values():
        if any(facts.has_file(marker) for marker in pm.markers):
            return pm.id
    return None


def build_inventory(
    root: Path,
    registry: Registry,
    *,
    verify: bool = False,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    include_claude_code: bool = True,
) -> Inventory:
    """Inspect ``root`` and the user's environment. Read-only unless ``verify`` is set."""
    environ = os.environ if env is None else env
    facts = scan_project(root)
    facts.package_manager = detect_package_manager(facts, registry)
    warnings = list(facts.warnings)
    findings: list[Finding] = []

    for language, count in sorted(facts.languages.items(), key=lambda kv: -kv[1]):
        findings.append(
            Finding(
                id=f"language:{language}",
                kind="language",
                name=language,
                status=Status.AVAILABLE_VERIFIED,
                evidence=[f"{count} source file(s)"],
            )
        )
    for name in facts.build_files:
        findings.append(
            Finding(
                id=f"build:{name}",
                kind="build_system",
                name=BUILD_FILES[name],
                status=Status.AVAILABLE_VERIFIED,
                evidence=[f"{name} present"],
            )
        )
    if facts.is_git_repo:
        evidence = ["repository detected (.git)"]
        capabilities = ["version_control"]
        if any(is_github_remote(remote) for remote in facts.git_remotes):
            evidence.append("GitHub remote configured")
        findings.append(
            Finding(
                id="vcs:git-repository",
                kind="vcs",
                name="git repository",
                status=Status.AVAILABLE_VERIFIED,
                capabilities=capabilities,
                evidence=evidence,
                detail={"remotes": facts.git_remotes},
            )
        )

    cache: dict[str, str | None] = {}

    def cached_lookup(name: str) -> str | None:
        if name not in cache:
            cache[name] = resolve_executable(name, root)
        return cache[name]

    environment = detect_environment(registry, facts, cached_lookup)
    if environment is not None:
        findings.append(environment)

    for tool in registry.tools.values():
        finding = detect_tool(tool, facts, environ, verify, which=cached_lookup)
        if finding is not None:
            findings.append(finding)

    if include_claude_code:
        # Imported here to avoid a package import cycle (the adapter uses discovery.models).
        from susanoo.adapters.claude_code.discovery import discover_claude_code

        findings += discover_claude_code(root, home or Path.home(), registry, warnings)

    return Inventory(
        root=str(root),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        verified=verify,
        languages=dict(sorted(facts.languages.items(), key=lambda kv: -kv[1])),
        findings=findings,
        warnings=warnings,
        source_files=facts.source_files,
        package_manager=facts.package_manager,
        ui_domains=detect_domains(facts.npm_packages, facts.has_file),
        design_files=detect_design_files(root, facts.npm_packages),
        architecture_files=detect_architecture_files(root),
    )


def has_test_framework(inventory: Inventory) -> bool:
    return any(
        f.status.usable or f.status is Status.CONFIGURED_INACCESSIBLE
        for f in inventory.for_capability("unit_testing")
        if f.kind == "tool"
    )
