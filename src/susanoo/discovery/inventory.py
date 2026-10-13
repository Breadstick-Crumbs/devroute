"""Capability Intelligence Engine: build a structured inventory of the environment."""

from __future__ import annotations

import os

# Bandit B404: only used for opt-in `--version` probes of registry tools.
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from susanoo.discovery.executables import (
    PROJECT_ENV_DIRS,
    describe_location,
    has_project_python_env,
    resolve_executable,
)
from susanoo.discovery.models import Finding, Inventory, Status
from susanoo.discovery.project import BUILD_FILES, ProjectFacts, normalize_package, scan_project
from susanoo.registry import Registry, ToolSpec

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

    def lookup(name: str) -> str | None:
        return which(name) if which is not None else resolve_executable(name, facts.root)

    evidence: list[str] = []
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
        if lookup("npm"):
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
        if any("github.com" in remote for remote in facts.git_remotes):
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
    )


def has_test_framework(inventory: Inventory) -> bool:
    return any(
        f.status.usable or f.status is Status.CONFIGURED_INACCESSIBLE
        for f in inventory.for_capability("unit_testing")
        if f.kind == "tool"
    )
