"""Read-only discovery of Claude Code skills, commands, subagents, MCP servers, and plugins.

Only names, scopes, transports, and parsed descriptions are recorded. Environment
variables, headers, and arguments of MCP server entries are never read into the
inventory because they commonly contain credentials.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from devroute.discovery.models import Finding, Status
from devroute.registry import Registry

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
MAX_DEFINITION_BYTES = 512_000


class FrontmatterError(ValueError):
    pass


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the simple YAML subset used by Claude Code definition files.

    Supports ``key: value`` pairs, quoted values, and ``|`` / ``>`` block scalars.
    Lists and nested mappings are kept as their raw text. Raises
    ``FrontmatterError`` when no frontmatter block is present.
    """
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if not match:
        raise FrontmatterError("no YAML frontmatter block")
    result: dict[str, str] = {}
    lines = (match.group(1) or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        key_match = _KEY.match(line)
        if not key_match:
            raise FrontmatterError(f"cannot parse frontmatter line: {line.strip()[:60]}")
        key, value = key_match.group(1), key_match.group(2).strip()
        if value == "" and i < len(lines) and lines[i].startswith("- "):
            # Unindented YAML sequence: "tools:" followed by "- Read" lines.
            items: list[str] = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:].strip())
                i += 1
            value = ", ".join(items)
        elif value in ("|", ">", "|-", ">-", "|+", ">+") or value == "":
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(part for part in block).strip()
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def infer_capabilities(text: str, registry: Registry) -> list[str]:
    """Infer capabilities from free text using the registry's capability keywords."""
    lowered = text.lower()
    found: list[str] = []
    for spec in registry.capabilities.values():
        for keyword in spec.keywords:
            pattern = rf"\b{re.escape(keyword)}" + (r"\b" if len(keyword) <= 3 else "")
            if re.search(pattern, lowered):
                found.append(spec.id)
                break
    return found


def _read_definition(path: Path) -> tuple[dict[str, str] | None, str | None]:
    try:
        if path.stat().st_size > MAX_DEFINITION_BYTES:
            return None, "definition file is unexpectedly large"
        return parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")), None
    except FrontmatterError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"cannot read ({exc.strerror or exc})"


def _definition_finding(kind: str, name: str, path: Path, scope: str, registry: Registry) -> Finding:
    meta, error = _read_definition(path)
    if meta is None:
        return Finding(
            id=f"{kind}:{scope}:{name}",
            kind=kind,
            name=name,
            status=Status.CONFIGURED_INACCESSIBLE,
            evidence=[f"{path}: {error}"],
            scope=scope,
        )
    display = meta.get("name", name)
    description = meta.get("description", "")
    evidence = [f"definition parsed: {path}"]
    status = Status.AVAILABLE_VERIFIED
    if kind in ("skill", "agent") and not description:
        status = Status.INSTALLED_UNVERIFIED
        evidence.append("no description in frontmatter; Claude may not select it automatically")
    detail: dict[str, Any] = {"description": description[:300]}
    for key in ("tools", "model", "allowed-tools", "disable-model-invocation", "argument-hint", "isolation"):
        if key in meta:
            detail[key] = meta[key][:200]
    return Finding(
        id=f"{kind}:{scope}:{display}",
        kind=kind,
        name=display,
        status=status,
        capabilities=infer_capabilities(f"{display} {description}", registry),
        evidence=evidence,
        scope=scope,
        detail=detail,
    )


def discover_skills(base: Path, scope: str, registry: Registry) -> list[Finding]:
    findings: list[Finding] = []
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                findings.append(_definition_finding("skill", entry.name, skill_file, scope, registry))
            else:
                findings.append(
                    Finding(
                        id=f"skill:{scope}:{entry.name}",
                        kind="skill",
                        name=entry.name,
                        status=Status.CONFIGURED_INACCESSIBLE,
                        evidence=[f"{entry}: directory has no SKILL.md"],
                        scope=scope,
                    )
                )
    commands_dir = base / "commands"
    if commands_dir.is_dir():
        for path in sorted(commands_dir.rglob("*.md")):
            name = ":".join(path.relative_to(commands_dir).with_suffix("").parts)
            finding = _definition_finding("command", name, path, scope, registry)
            if finding.status is Status.CONFIGURED_INACCESSIBLE:
                # Commands without frontmatter are valid; the whole file is the prompt.
                finding.status = Status.AVAILABLE_VERIFIED
                finding.evidence = [f"command file: {path}"]
            findings.append(finding)
    return findings


def discover_agents(base: Path, scope: str, registry: Registry) -> list[Finding]:
    agents_dir = base / "agents"
    if not agents_dir.is_dir():
        return []
    return [
        _definition_finding("agent", path.stem, path, scope, registry)
        for path in sorted(agents_dir.rglob("*.md"))
    ]


def _load_json(path: Path, warnings: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{path}: cannot parse ({exc})")
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _mcp_finding(name: str, entry: Any, scope: str, disabled: set[str], registry: Registry) -> Finding:
    capabilities = infer_capabilities(name, registry)
    lowered = name.lower()
    for tool in registry.tools.values():
        if tool.kind == "mcp" and any(p in lowered for p in tool.mcp_patterns):
            capabilities += [c for c in tool.capabilities if c not in capabilities]
    finding = Finding(
        id=f"mcp:{scope}:{name}",
        kind="mcp_server",
        name=name,
        status=Status.INSTALLED_UNVERIFIED,
        capabilities=capabilities,
        scope=scope,
        tool_kind="mcp",
    )
    if not isinstance(entry, dict):
        finding.status = Status.CONFIGURED_INACCESSIBLE
        finding.evidence.append("server entry is not an object")
        return finding
    raw_type = entry.get("type")
    transport = (
        raw_type if isinstance(raw_type, str) and raw_type else ("http" if "url" in entry else "stdio")
    )
    finding.detail["transport"] = transport
    if name in disabled:
        finding.status = Status.CONFIGURED_INACCESSIBLE
        finding.evidence.append("disabled for this project in Claude Code settings")
    elif transport == "stdio":
        # Only the executable's basename is ever shown: full commands and paths can embed secrets.
        raw = entry.get("command")
        parts = raw.split() if isinstance(raw, str) else []
        executable = parts[0] if parts else ""
        label = Path(executable).name if executable else "(none)"
        finding.detail["command"] = label
        if executable and (shutil.which(executable) or Path(executable).is_file()):
            finding.evidence.append(f"launch command '{label}' resolvable; server not started")
        else:
            finding.status = Status.CONFIGURED_INACCESSIBLE
            finding.evidence.append(f"launch command '{label}' not found")
    else:
        finding.evidence.append(f"remote {transport} server configured; not contacted")
    return finding


def discover_mcp_servers(root: Path, home: Path, registry: Registry, warnings: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    user_config = _as_dict(_load_json(home / ".claude.json", warnings))
    project_entry: dict[str, Any] = {}
    target = _norm_path(str(root))
    for key, value in _as_dict(user_config.get("projects")).items():
        if isinstance(value, dict) and _norm_path(str(key)) == target:
            project_entry = value
            break
    raw_disabled = project_entry.get("disabledMcpjsonServers")
    disabled = {d for d in raw_disabled if isinstance(d, str)} if isinstance(raw_disabled, list) else set()

    project_file = _as_dict(_load_json(root / ".mcp.json", warnings))
    sources: tuple[tuple[dict[str, Any], str, set[str]], ...] = (
        (project_file, "project", disabled),
        (project_entry, "local", set()),
        (user_config, "user", set()),
    )
    for source, scope, off in sources:
        servers = source.get("mcpServers")
        if servers is not None and not isinstance(servers, dict):
            warnings.append(f"{scope} MCP configuration: 'mcpServers' is not an object; ignored")
            continue
        for name, entry in _as_dict(servers).items():
            findings.append(_mcp_finding(str(name), entry, scope, off, registry))
    return findings


def discover_plugins(root: Path, home: Path, registry: Registry, warnings: list[str]) -> list[Finding]:
    enabled: dict[str, str] = {}
    for scope, path in (
        ("user", home / ".claude" / "settings.json"),
        ("project", root / ".claude" / "settings.json"),
        ("local", root / ".claude" / "settings.local.json"),
    ):
        data = _load_json(path, warnings)
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        if isinstance(plugins, dict):
            for plugin_id, on in plugins.items():
                if on is True:
                    enabled[plugin_id] = scope
                else:
                    enabled.pop(plugin_id, None)
    return [
        Finding(
            id=f"plugin:{scope}:{plugin_id}",
            kind="plugin",
            name=plugin_id,
            status=Status.INSTALLED_UNVERIFIED,
            capabilities=infer_capabilities(plugin_id.replace("-", " "), registry),
            evidence=[f"enabled in {scope} settings"],
            scope=scope,
        )
        for plugin_id, scope in sorted(enabled.items())
    ]


def discover_claude_code(root: Path, home: Path, registry: Registry, warnings: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for base, scope in ((root / ".claude", "project"), (home / ".claude", "user")):
        findings += discover_skills(base, scope, registry)
        findings += discover_agents(base, scope, registry)
    findings += discover_mcp_servers(root, home, registry, warnings)
    findings += discover_plugins(root, home, registry, warnings)
    return findings
