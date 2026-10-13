from __future__ import annotations

import json
from pathlib import Path

from susanoo.adapters.claude_code.discovery import parse_frontmatter
from susanoo.discovery import Status, build_inventory, detect_tool, scan_project
from susanoo.discovery.inventory import detect_environment
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

    from susanoo.discovery.executables import describe_location, resolve_executable

    bindir = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    name = "susanoo-fake-tool"
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


def test_declared_capabilities_override_inference(tmp_path: Path, home: Path, registry):
    root = tmp_path / "proj"
    write(
        root,
        ".claude/agents/researcher.md",
        "---\nname: researcher\ndescription: Researches browser testing and payment tools\n"
        "susanoo-capabilities: web_research, not_a_capability\n---\nx",
    )
    [agent] = build_inventory(root, registry, home=home, env={}).by_kind("agent")
    assert agent.capabilities == ["web_research"]
    assert any("not_a_capability" in e for e in agent.evidence)


def poetry_project(root: Path) -> Path:
    write(root, "pyproject.toml", '[tool.poetry]\nname = "demo"\nversion = "0.1.0"\n')
    write(root, "poetry.lock", "")
    write(root, "tests/test_x.py", "def test_x():\n    assert True\n")
    return root


def test_poetry_environment_outside_project_is_detected(tmp_path: Path, registry):
    facts = scan_project(poetry_project(tmp_path))
    finding = detect_environment(registry, facts, all_binaries)
    assert finding is not None and finding.id == "environment:poetry"
    assert finding.status is Status.INSTALLED_UNVERIFIED
    assert finding.detail["run_prefix"] == ["poetry", "run"]
    assert "poetry.lock present" in finding.evidence
    missing = detect_environment(registry, facts, no_binaries)
    assert missing is not None and missing.status is Status.CONFIGURED_INACCESSIBLE


def test_pipenv_environment_is_detected(tmp_path: Path, registry):
    write(tmp_path, "Pipfile", "[packages]\n")
    finding = detect_environment(registry, scan_project(tmp_path), all_binaries)
    assert finding is not None and finding.id == "environment:pipenv"
    assert finding.detail["run_prefix"] == ["pipenv", "run"]


def test_in_project_environment_needs_no_manager(tmp_path: Path, registry):
    import os

    poetry_project(tmp_path)
    (tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    assert detect_environment(registry, scan_project(tmp_path), all_binaries) is None


def test_env_bin_directory_counts_as_in_project_environment(tmp_path: Path, registry):
    poetry_project(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    assert detect_environment(registry, scan_project(tmp_path), all_binaries) is None


def test_uv_lock_overrides_leftover_poetry_lock(tmp_path: Path, registry):
    poetry_project(tmp_path)
    write(tmp_path, "uv.lock", "")
    assert detect_environment(registry, scan_project(tmp_path), all_binaries) is None


def test_project_registry_cannot_add_environments(tmp_path: Path):
    from susanoo.registry import load_registry

    write(
        tmp_path,
        ".susanoo/tools.toml",
        '[[environments]]\nid = "evil"\nbinary = "sh"\nrun_prefix = ["sh", "-c"]\nmarkers = ["app.py"]\n',
    )
    assert [e.id for e in load_registry(tmp_path).environments] == ["poetry", "pipenv"]


def test_invalid_environment_entries_are_rejected():
    import pytest

    from susanoo.config import ConfigError
    from susanoo.registry import _environment_from_dict

    for entry in (
        {"binary": "poetry", "run_prefix": ["poetry", "run"], "markers": ["poetry.lock"]},
        {"id": "x", "run_prefix": ["x"], "markers": ["x.lock"]},
        {"id": "x", "binary": "x", "run_prefix": [], "markers": ["x.lock"]},
        {"id": "x", "binary": "x", "run_prefix": ["x"]},
        {"id": "x", "binary": "x", "run_prefix": "x run", "markers": ["x.lock"]},
    ):
        with pytest.raises(ConfigError):
            _environment_from_dict(entry)


def test_inventory_lists_environment_manager_only_when_present(tmp_path: Path, home: Path, registry):
    from susanoo.render import render_inventory

    plain = build_inventory(tmp_path, registry, home=home, env={})
    assert "Python environment manager" not in render_inventory(plain)
    inv = build_inventory(poetry_project(tmp_path), registry, home=home, env={})
    assert "## Python environment manager" in render_inventory(inv)


def test_plain_python_project_has_no_environment_manager(python_project: Path, registry):
    assert detect_environment(registry, scan_project(python_project), all_binaries) is None


def test_build_inventory_reports_environment(tmp_path: Path, home: Path, registry):
    inv = build_inventory(poetry_project(tmp_path), registry, home=home, env={})
    assert [f.id for f in inv.by_kind("environment")] == ["environment:poetry"]
