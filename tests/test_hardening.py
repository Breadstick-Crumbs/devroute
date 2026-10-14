"""Regression tests for untrusted-input handling: crafted repositories must not leak
secrets, execute code during discovery, crash the CLI, or make the installer touch
files outside its own targets."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from susanoo.adapters.claude_code.discovery import parse_frontmatter
from susanoo.adapters.claude_code.installer import (
    apply_install,
    apply_uninstall,
    plan_install,
    plan_uninstall,
    sha256,
)
from susanoo.discovery import Status, build_inventory, detect_tool, scan_project
from susanoo.discovery.project import is_github_remote
from susanoo.registry import load_registry
from susanoo.verification import redact
from susanoo.verification.executor import run_command
from tests.conftest import write


def test_project_tools_cannot_define_version_probes(tmp_path: Path):
    write(
        tmp_path,
        ".susanoo/tools.toml",
        '[[tools]]\nid = "evil"\nkind = "cli"\ncapabilities = ["unit_testing"]\n'
        'binaries = ["python"]\nversion_args = ["-c", "open(\'pwned\', \'w\')"]\n',
    )
    registry = load_registry(tmp_path)
    assert registry.tools["evil"].origin == "project"
    assert registry.tools["evil"].version_args == ()
    build_inventory(tmp_path, registry, verify=True, home=tmp_path / "home", env={})
    assert not (tmp_path / "pwned").exists()


def test_verify_never_runs_executables_inside_the_project(tmp_path: Path, registry):
    facts = scan_project(tmp_path)
    inside = str(tmp_path / "node_modules" / ".bin" / "git")
    finding = detect_tool(registry.tools["git"], facts, {}, True, which=lambda name: inside)
    assert finding is not None
    assert finding.status is Status.INSTALLED_UNVERIFIED
    assert any("skipped" in e for e in finding.evidence)


def test_uninstall_ignores_manifest_paths_outside_targets(tmp_path: Path):
    root = tmp_path / "proj"
    victim = tmp_path / "victim" / "sub" / "empty.txt"
    write(tmp_path, "victim/sub/empty.txt", "")
    apply_install(root, plan_install(root))
    manifest_path = root / ".susanoo" / "install.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["../victim/sub/empty.txt"] = sha256("")
    manifest["files"][str(victim)] = sha256("")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    actions = plan_uninstall(root)
    assert all(not a.path.startswith("..") and not os.path.isabs(a.path) for a in actions)
    apply_uninstall(root, actions)
    assert victim.exists()


def test_installer_handles_directory_at_target_path(tmp_path: Path):
    (tmp_path / ".claude" / "skills" / "susanoo" / "SKILL.md").mkdir(parents=True)
    actions = {a.path: a for a in plan_install(tmp_path, force=True)}
    assert actions[".claude/skills/susanoo/SKILL.md"].action == "conflict"


@pytest.mark.parametrize(
    "command",
    [
        "docker run -e API_KEY=sk-live-SECRET123 image",
        "/opt/token=SECRET123/server-bin",
        "C:\\Users\\x\\token=SECRET123\\srv.exe --flag",
    ],
)
def test_mcp_commands_never_echo_secrets(tmp_path: Path, home: Path, command: str):
    root = tmp_path / "proj"
    write(root, ".mcp.json", {"mcpServers": {"svc": {"command": command}}})
    inv = build_inventory(root, load_registry(), home=home, env={})
    dumped = json.dumps(inv.to_dict())
    assert "SECRET123" not in dumped
    assert "sk-live" not in dumped


@pytest.mark.parametrize(
    "mcp_json, claude_json",
    [
        ({"mcpServers": ["x"]}, None),
        ({"mcpServers": "x"}, None),
        ([1, 2, 3], None),
        (None, {"projects": ["a"]}),
        (None, {"mcpServers": "str"}),
        (None, {"projects": {"PLACEHOLDER": {"disabledMcpjsonServers": [{}], "mcpServers": []}}}),
        (None, {"projects": {"PLACEHOLDER": {"disabledMcpjsonServers": "abc"}}}),
        ({"mcpServers": {"s": {"type": 5, "command": ["a"]}}}, None),
    ],
)
def test_malformed_claude_configuration_does_not_crash(tmp_path: Path, home: Path, mcp_json, claude_json):
    root = tmp_path / "proj"
    root.mkdir()
    if mcp_json is not None:
        write(root, ".mcp.json", json.dumps(mcp_json))
    if claude_json is not None:
        text = json.dumps(claude_json).replace("PLACEHOLDER", str(root).replace("\\", "\\\\"))
        write(home, ".claude.json", text)
    build_inventory(root, load_registry(), home=home, env={})


@pytest.mark.parametrize(
    "pyproject",
    [
        'project = "x"\n',
        "tool = 1\n",
        "[project]\noptional-dependencies = []\n",
        '[tool.poetry.group]\ndev = "x"\n',
        '[project]\ndependencies = "requests"\n',
    ],
)
def test_malformed_pyproject_does_not_crash(tmp_path: Path, pyproject: str):
    write(tmp_path, "pyproject.toml", pyproject)
    facts = scan_project(tmp_path)
    assert not {"e", "q", "r", "s", "t", "u"} & facts.python_packages


@pytest.mark.parametrize(
    "remote",
    [
        "ssh://git:TOKEN2@host/repo.git",
        "HTTPS://u:TOKEN2@github.com/o/r.git",
        "https://user:p@TOKEN2@github.com/o/r.git",
    ],
)
def test_git_remote_userinfo_fully_stripped(tmp_path: Path, remote: str):
    write(tmp_path, ".git/config", f'[remote "origin"]\n\turl = {remote}\n')
    [cleaned] = scan_project(tmp_path).git_remotes
    assert "TOKEN2" not in cleaned and "@" not in cleaned


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("https://github.com/o/r.git", True),
        ("HTTPS://GitHub.com/o/r.git", True),
        ("git@github.com:o/r.git", True),
        ("ssh://git@ssh.github.com:443/o/r.git", True),
        ("https://github.com.evil.example/o/r.git", False),
        ("https://evil.example/github.com/o/r.git", False),
        ("https://notgithub.com/o/r.git", False),
        ("git@evil.example:github.com/r.git", False),
        ("../github.com/r.git", False),
        ("C:/src/github.com/r", False),
    ],
)
def test_github_remote_matches_host_only(remote: str, expected: bool):
    assert is_github_remote(remote) is expected


@pytest.mark.parametrize(
    "text, secret",
    [
        ("Authorization: Bearer abc123xyz", "abc123xyz"),
        ('"api_key": "sk-abcdef"', "sk-abcdef"),
        ("AWS_SECRET_ACCESS_KEY=abcd1234", "abcd1234"),
        ("token github_pat_" + "A" * 30, "A" * 30),
        ("connecting to postgres://user:hunter2@db:5432/x", "hunter2"),
        ("key sk-proj-" + "b" * 24, "b" * 24),
    ],
)
def test_redaction_covers_common_formats(text: str, secret: str):
    assert secret not in redact(text)


def test_frontmatter_accepts_lists_and_empty_blocks():
    meta = parse_frontmatter("---\nname: a\ntools:\n- Read\n- Grep\ndescription: d\n---\nbody")
    assert meta == {"name": "a", "tools": "Read, Grep", "description": "d"}
    assert parse_frontmatter("---\n---\nbody") == {}


def test_timeout_kills_child_processes(tmp_path: Path):
    marker = tmp_path / "grandchild-finished"
    grandchild = f"import time, pathlib; time.sleep(8); pathlib.Path({str(marker)!r}).write_text('x')"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
    )
    started = time.monotonic()
    status, code, _, _ = run_command([sys.executable, "-c", parent], tmp_path, timeout=1.5)
    elapsed = time.monotonic() - started
    assert status == "timeout" and code is None
    assert elapsed < 8, f"timeout took {elapsed:.1f}s; child processes were not stopped"
    time.sleep(8)
    assert not marker.exists(), "grandchild kept running after the timeout"
