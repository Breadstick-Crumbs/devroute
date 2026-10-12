from __future__ import annotations

import json
from pathlib import Path

from devroute.adapters.claude_code.discovery import parse_frontmatter
from devroute.discovery import Status, build_inventory, detect_tool, scan_project
from tests.conftest import write


def no_binaries(name: str) -> str | None:
    return None


def all_binaries(name: str) -> str | None:
    return f"/usr/bin/{name}"


def test_scan_python_project(python_project: Path):
    facts = scan_project(python_project)
    assert facts.languages["python"] == 3
    assert {"pytest", "ruff", "requests"} <= facts.python_packages
    assert "pytest" in facts.pyproject_tools
    assert facts.is_git_repo
    assert "pyproject.toml" in facts.build_files


def test_scan_skips_dependency_directories(tmp_path: Path):
    write(tmp_path, "app.js", "")
    write(tmp_path, "node_modules/lib/index.js", "")
    write(tmp_path, ".venv/lib/x.py", "")
    facts = scan_project(tmp_path)
    assert facts.languages == {"javascript": 1}


def test_scan_node_project(tmp_path: Path):
    write(
        tmp_path,
        "package.json",
        {
            "scripts": {"test": "vitest run"},
            "devDependencies": {"vitest": "^1", "@playwright/test": "^1", "typescript": "^5"},
        },
    )
    write(tmp_path, "tsconfig.json", "{}")
    write(tmp_path, "src/index.ts", "")
    write(tmp_path, "Makefile", "build:\n\techo hi\ntest: build\n\techo test\n")
    facts = scan_project(tmp_path)
    assert facts.npm_scripts == {"test": "vitest run"}
    assert {"vitest", "@playwright/test"} <= facts.npm_packages
    assert {"build", "test"} <= facts.make_targets


def test_git_remote_credentials_are_redacted(tmp_path: Path):
    write(tmp_path, ".git/config", '[remote "origin"]\n\turl = https://user:s3cret@github.com/o/r.git\n')
    facts = scan_project(tmp_path)
    assert facts.git_remotes == ["https://github.com/o/r.git"]


def test_tool_statuses(python_project: Path, registry):
    facts = scan_project(python_project)
    pytest_tool = registry.tools["pytest"]
    # Declared and configured but not runnable here -> configured but inaccessible.
    assert (
        detect_tool(pytest_tool, facts, {}, False, which=no_binaries).status is Status.CONFIGURED_INACCESSIBLE
    )
    found = detect_tool(pytest_tool, facts, {}, False, which=all_binaries)
    assert found.status is Status.INSTALLED_UNVERIFIED
    assert found.scope == "project"
    # Unrelated tool with nothing on PATH -> not reported at all.
    assert detect_tool(registry.tools["k6"], facts, {}, False, which=no_binaries) is None


def test_npm_package_without_node_modules_is_inaccessible(tmp_path: Path, registry):
    write(tmp_path, "package.json", {"devDependencies": {"vitest": "^1"}})
    facts = scan_project(tmp_path)
    finding = detect_tool(registry.tools["vitest"], facts, {}, False, which=no_binaries)
    assert finding.status is Status.CONFIGURED_INACCESSIBLE
    (tmp_path / "node_modules" / "vitest").mkdir(parents=True)
    assert (
        detect_tool(registry.tools["vitest"], facts, {}, False, which=no_binaries).status
        is Status.INSTALLED_UNVERIFIED
    )


def test_native_tools_verified_inside_claude_code_session(tmp_path: Path, registry):
    facts = scan_project(tmp_path)
    finding = detect_tool(
        registry.tools["claude-code-subagents"], facts, {"CLAUDECODE": "1"}, False, which=no_binaries
    )
    assert finding.status is Status.AVAILABLE_VERIFIED


def test_frontmatter_parser():
    text = (
        '---\nname: demo\ndescription: >\n  Folded\n  text\nallowed-tools: Read, Grep\nq: "quoted"\n---\nBody'
    )
    meta = parse_frontmatter(text)
    assert meta == {
        "name": "demo",
        "description": "Folded text",
        "allowed-tools": "Read, Grep",
        "q": "quoted",
    }


def test_claude_code_definitions_are_discovered(tmp_path: Path, home: Path, registry):
    root = tmp_path / "proj"
    write(
        root, ".claude/skills/sec/SKILL.md", "---\nname: sec\ndescription: Security review of diffs\n---\nx"
    )
    write(root, ".claude/skills/broken/notes.txt", "no skill file")
    write(root, ".claude/agents/reviewer.md", "---\nname: reviewer\ndescription: Code review agent\n---\nx")
    write(root, ".claude/commands/ops/deploy.md", "Deploy the app")
    write(home, ".claude/skills/personal/SKILL.md", "no frontmatter")
    inv = build_inventory(root, registry, home=home, env={})
    skills = {f.name: f for f in inv.by_kind("skill")}
    assert skills["sec"].status is Status.AVAILABLE_VERIFIED
    assert "security_analysis" in skills["sec"].capabilities
    assert skills["broken"].status is Status.CONFIGURED_INACCESSIBLE
    assert skills["personal"].status is Status.CONFIGURED_INACCESSIBLE
    assert skills["personal"].scope == "user"
    assert inv.by_kind("agent")[0].capabilities == ["code_review"]
    assert inv.by_kind("command")[0].name == "ops:deploy"


def test_mcp_servers_never_expose_secrets(tmp_path: Path, home: Path, registry):
    root = tmp_path / "proj"
    write(
        root,
        ".mcp.json",
        {
            "mcpServers": {
                "playwright": {
                    "command": "definitely-not-a-real-binary-xyz",
                    "args": ["--token", "ARGSECRET"],
                    "env": {"API_KEY": "ENVSECRET"},
                },
                "remote-db": {
                    "type": "http",
                    "url": "https://example.invalid/mcp",
                    "headers": {"Authorization": "Bearer HEADERSECRET"},
                },
            }
        },
    )
    write(
        home,
        ".claude.json",
        {
            "mcpServers": {"stripe": {"type": "http", "url": "https://example.invalid"}},
            "projects": {str(root): {"disabledMcpjsonServers": ["remote-db"], "mcpServers": {}}},
        },
    )
    inv = build_inventory(root, registry, home=home, env={})
    servers = {f.name: f for f in inv.by_kind("mcp_server")}
    assert servers["playwright"].status is Status.CONFIGURED_INACCESSIBLE
    assert "browser_testing" in servers["playwright"].capabilities
    assert servers["remote-db"].status is Status.CONFIGURED_INACCESSIBLE  # disabled for project
    assert servers["stripe"].scope == "user"
    assert "payment_sandbox" in servers["stripe"].capabilities
    dumped = json.dumps(inv.to_dict())
    for secret in ("ARGSECRET", "ENVSECRET", "HEADERSECRET", "example.invalid"):
        assert secret not in dumped


def test_enabled_plugins_are_listed(tmp_path: Path, home: Path, registry):
    root = tmp_path / "proj"
    write(
        home,
        ".claude/settings.json",
        {"enabledPlugins": {"security-guidance@market": True, "off@market": False}},
    )
    inv = build_inventory(root, registry, home=home, env={})
    assert [p.name for p in inv.by_kind("plugin")] == ["security-guidance@market"]


def test_unreadable_json_becomes_warning(tmp_path: Path, home: Path, registry):
    root = tmp_path / "proj"
    write(root, ".mcp.json", "{not json")
    inv = build_inventory(root, registry, home=home, env={})
    assert any(".mcp.json" in w for w in inv.warnings)


def test_project_environment_tools_take_precedence(tmp_path: Path):
    import os
    import stat

    from devroute.discovery.executables import describe_location, resolve_executable

    bindir = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    name = "devroute-fake-tool"
    script = bindir / (name + (".bat" if os.name == "nt" else ""))
    script.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    found = resolve_executable(name, tmp_path)
    assert found is not None and Path(found).parent == bindir
    assert describe_location(found, tmp_path) == "in project environment .venv"
    assert resolve_executable(name, tmp_path / "elsewhere") is None


def test_subdirectory_of_repository_counts_as_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "packages" / "app"
    sub.mkdir(parents=True)
    assert scan_project(sub).is_git_repo
