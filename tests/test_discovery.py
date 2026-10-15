from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


def _fake_executable(folder: Path, name: str, first_line: str = "") -> Path:
    import os
    import stat

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + (".bat" if os.name == "nt" else ""))
    path.write_text(first_line or ("@echo off\n" if os.name == "nt" else "#!/bin/sh\n"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def path_only(monkeypatch: pytest.MonkeyPatch):
    """PATH is exactly the given folders, with no cached lookups."""
    import os

    from susanoo.discovery import executables

    monkeypatch.setattr(executables, "_cache", None)

    def set_path(*folders: Path) -> None:
        monkeypatch.setenv("PATH", os.pathsep.join(str(f) for f in folders))

    return set_path


def test_module_runs_under_the_interpreter_that_owns_the_tool(tmp_path: Path, path_only):
    from susanoo.discovery.executables import resolve_module_interpreter

    # pipx / uv tool: the tool's own folder has its interpreter; the first `python` on PATH lacks it.
    other, tool_bin = tmp_path / "system", tmp_path / "pipx-venv" / "bin"
    _fake_executable(other, "python")
    owner = _fake_executable(tool_bin, "python")
    _fake_executable(tool_bin, "faketool")
    path_only(other, tool_bin)
    assert Path(resolve_module_interpreter("faketool", tmp_path / "project")) == owner

    # A Windows install: Scripts/faketool.exe, with python.exe one folder up.
    base = tmp_path / "Python311"
    _fake_executable(base / "Scripts", "faketool")
    owner = _fake_executable(base, "python")
    path_only(other, base / "Scripts")
    assert Path(resolve_module_interpreter("faketool", tmp_path / "project")) == owner

    # No tool found: `python` as usual.
    path_only(other)
    assert Path(resolve_module_interpreter("missingtool", tmp_path / "project")).parent == other


def test_module_interpreter_prefers_the_project_environment(tmp_path: Path, path_only):
    import os

    from susanoo.discovery.executables import resolve_module_interpreter

    tool_bin = tmp_path / "pipx-venv" / "bin"
    _fake_executable(tool_bin, "faketool")
    _fake_executable(tool_bin, "python")
    venv = _fake_executable(
        tmp_path / "project" / ".venv" / ("Scripts" if os.name == "nt" else "bin"), "python"
    )
    path_only(tool_bin)
    assert Path(resolve_module_interpreter("faketool", tmp_path / "project")) == venv


@pytest.mark.skipif(sys.platform == "win32", reason="console scripts are .exe launchers on Windows")
def test_module_interpreter_follows_the_script_shebang(tmp_path: Path, path_only):
    from susanoo.discovery.executables import resolve_module_interpreter

    # Homebrew-style: the script's folder has no python; its #! line names the interpreter.
    interpreter = _fake_executable(tmp_path / "libexec" / "bin", "python3.12")
    _fake_executable(tmp_path / "bin", "faketool", f"#!{interpreter}\n")
    path_only(tmp_path / "bin")
    assert Path(resolve_module_interpreter("faketool", tmp_path / "project")) == interpreter


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


def test_pipfile_dependencies_are_parsed(tmp_path: Path):
    write(tmp_path, "Pipfile", '[packages]\nRequests = "*"\n\n[dev-packages]\npytest = "*"\nruff = "*"\n')
    assert {"requests", "pytest", "ruff"} <= scan_project(tmp_path).python_packages
    write(tmp_path, "Pipfile", "[packages\n")
    facts = scan_project(tmp_path)
    assert any("Pipfile: invalid TOML" in w for w in facts.warnings)


def test_pipenv_dev_dependency_runs_through_pipenv(tmp_path: Path, registry):
    from susanoo.analyzer import CapabilityRequirement
    from susanoo.discovery import Inventory
    from susanoo.selection import recommend

    write(tmp_path, "Pipfile", '[dev-packages]\npytest = "*"\n')
    write(tmp_path, "app.py", "")
    facts = scan_project(tmp_path)

    def only_pipenv(name: str) -> str | None:
        return "/usr/bin/pipenv" if name == "pipenv" else None

    pytest_finding = detect_tool(registry.tools["pytest"], facts, {}, False, which=only_pipenv)
    assert pytest_finding is not None and pytest_finding.status is Status.CONFIGURED_INACCESSIBLE
    environment = detect_environment(registry, facts, only_pipenv)
    assert environment is not None
    inv = Inventory(str(tmp_path), "", False, facts.languages, [pytest_finding, environment])
    [rec] = recommend([CapabilityRequirement("unit_testing", "required")], inv, registry)
    assert rec.selected is not None
    assert rec.selected.command == ["pipenv", "run", "python", "-m", "pytest", "-q"]


def test_poetry_detected_from_pyproject_without_lock(tmp_path: Path, registry):
    write(tmp_path, "pyproject.toml", '[tool.poetry]\nname = "demo"\n')
    finding = detect_environment(registry, scan_project(tmp_path), all_binaries)
    assert finding is not None and finding.id == "environment:poetry"
    assert finding.evidence[0] == "[tool.poetry] in pyproject.toml"


def test_poetry_wins_over_pipfile(tmp_path: Path, registry):
    poetry_project(tmp_path)
    write(tmp_path, "Pipfile", "[packages]\n")
    finding = detect_environment(registry, scan_project(tmp_path), all_binaries)
    assert finding is not None and finding.id == "environment:poetry"


def test_verify_dry_run_shows_withheld_reason(tmp_path: Path, monkeypatch):
    import io

    from susanoo import cli
    from susanoo.discovery import inventory

    def only_pytest(name: str, root: Path | None = None) -> str | None:
        return "/usr/bin/pytest" if name == "pytest" else None

    monkeypatch.setattr(inventory, "resolve_executable", only_pytest)
    poetry_project(tmp_path)
    write(tmp_path, "pytest.ini", "[pytest]\n")
    out = io.StringIO()
    code = cli.main(
        ["verify", "Fix the crash when saving settings", "--path", str(tmp_path)],
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == 0
    assert "unit_tests" in out.getvalue() and "'poetry' is not on PATH" in out.getvalue()


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


def test_package_manager_detection(tmp_path: Path, registry):
    from susanoo.discovery.inventory import detect_package_manager

    def facts_for(name: str, files: dict[str, str]):
        root = tmp_path / name
        for rel, content in files.items():
            write(root, rel, content)
        return scan_project(root)

    assert detect_package_manager(facts_for("none", {"app.py": ""}), registry) is None
    assert detect_package_manager(facts_for("bare", {"package.json": "{}"}), registry) is None
    assert (
        detect_package_manager(facts_for("pnpm", {"package.json": "{}", "pnpm-lock.yaml": ""}), registry)
        == "pnpm"
    )
    assert detect_package_manager(facts_for("bun", {"package.json": "{}", "bun.lock": ""}), registry) == "bun"
    assert (
        detect_package_manager(facts_for("npm", {"package.json": "{}", "package-lock.json": "{}"}), registry)
        == "npm"
    )
    # "packageManager" in package.json wins over a stale lockfile.
    declared = facts_for(
        "declared", {"package.json": '{"packageManager": "yarn@4.1.0"}', "package-lock.json": "{}"}
    )
    assert detect_package_manager(declared, registry) == "yarn"
    inv = build_inventory(tmp_path / "pnpm", registry, include_claude_code=False)
    assert inv.package_manager == "pnpm" and inv.to_dict()["package_manager"] == "pnpm"


def test_package_script_is_runnable_through_the_detected_manager(tmp_path: Path, registry):
    from susanoo.discovery.inventory import detect_tool

    root = tmp_path / "pnpm-app"
    write(root, "package.json", '{"scripts": {"test": "vitest"}}')
    write(root, "pnpm-lock.yaml", "")
    facts = scan_project(root)
    facts.package_manager = "pnpm"
    spec = registry.tools["npm-test-script"]
    only_npm = detect_tool(spec, facts, {}, False, which=lambda name: "/bin/npm" if name == "npm" else None)
    with_pnpm = detect_tool(
        spec, facts, {}, False, which=lambda name: "/bin/pnpm" if name == "pnpm" else None
    )
    assert only_npm is not None and not only_npm.status.usable
    assert with_pnpm is not None and with_pnpm.status.usable


def _site_packages(env: Path, *dist_infos: str) -> None:
    """An environment's site-packages with the given ``.dist-info`` folders (POSIX layout)."""
    for name in dist_infos:
        (env / "lib" / "python3.12" / "site-packages" / name).mkdir(parents=True)
    (env / "lib" / "python3.12" / "site-packages").mkdir(parents=True, exist_ok=True)


def test_pytest_xdist_is_detected_only_where_it_is_installed(tmp_path: Path, registry):
    spec = registry.tools["pytest-xdist"]
    root = tmp_path / "project"
    write(root, "pyproject.toml", '[project]\nname = "p"\nversion = "1"\n')
    pytest_in_venv = str(root / ".venv" / "bin" / "pytest")
    in_venv = lambda name: pytest_in_venv if name == "pytest" else None  # noqa: E731

    # Installed in the project's environment, even when not declared.
    _site_packages(root / ".venv", "pytest_xdist-3.6.1.dist-info", "pytest-8.3.4.dist-info")
    finding = detect_tool(spec, scan_project(root), {}, False, which=in_venv)
    assert finding is not None and finding.status is Status.INSTALLED_UNVERIFIED
    assert any("pytest-xdist installed" in e for e in finding.evidence)

    # Declared but missing from the environment that runs pytest: `-n auto` would be rejected.
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "p"\nversion = "1"\n[dependency-groups]\ndev = ["pytest-xdist"]\n',
    )
    (root / ".venv" / "lib" / "python3.12" / "site-packages" / "pytest_xdist-3.6.1.dist-info").rmdir()
    assert detect_tool(spec, scan_project(root), {}, False, which=in_venv) is None

    # Plain pytest is unaffected.
    assert detect_tool(registry.tools["pytest"], scan_project(root), {}, False, which=in_venv) is not None


def test_pytest_xdist_on_path_needs_a_declaration(tmp_path: Path, registry):
    spec = registry.tools["pytest-xdist"]
    root, system = tmp_path / "project", tmp_path / "system"
    write(root, "pyproject.toml", '[project]\nname = "p"\nversion = "1"\n')
    _site_packages(system, "pytest_xdist-3.6.1.dist-info")
    on_path = lambda name: str(system / "bin" / name) if name == "pytest" else None  # noqa: E731
    # Another environment's plugins are not the project's: they may be missing where tests run.
    assert detect_tool(spec, scan_project(root), {}, False, which=on_path) is None

    write(root, "requirements-dev.txt", "pytest\npytest-xdist>=3\n")
    finding = detect_tool(spec, scan_project(root), {}, False, which=on_path)
    assert finding is not None and finding.status is Status.INSTALLED_UNVERIFIED

    # Declared, but the pytest on PATH lacks it.
    (system / "lib" / "python3.12" / "site-packages" / "pytest_xdist-3.6.1.dist-info").rmdir()
    assert detect_tool(spec, scan_project(root), {}, False, which=on_path) is None


def test_pytest_xdist_in_an_external_environment_counts_when_declared(tmp_path: Path, registry):
    # Poetry/uv keep the environment outside the project: nothing to inspect, so the declaration decides.
    spec = registry.tools["pytest-xdist"]
    root = tmp_path / "project"
    write(root, "Pipfile", '[dev-packages]\npytest = "*"\npytest-xdist = "*"\n')
    finding = detect_tool(spec, scan_project(root), {}, False, which=no_binaries)
    assert finding is not None and finding.detail["python_declared"]
    write(root, "Pipfile", '[dev-packages]\npytest = "*"\n')
    assert detect_tool(spec, scan_project(root), {}, False, which=no_binaries) is None


def test_windows_environment_layout_is_inspected(tmp_path: Path):
    from susanoo.discovery.executables import python_package_installed

    env = tmp_path / "Python311"
    (env / "Lib" / "site-packages" / "pytest_xdist-3.6.1.dist-info").mkdir(parents=True)
    script = str(env / "Scripts" / "pytest.exe")
    assert python_package_installed("pytest-xdist", script) is True
    assert python_package_installed("pytest-cov", script) is False
    assert python_package_installed("pytest-xdist", str(tmp_path / "nowhere" / "bin" / "pytest")) is None


def test_pytest_xdist_needs_an_environment_it_can_inspect(tmp_path: Path, registry):
    # A pyenv/asdf shim or a launcher copied out of its environment: the folder beside it has no
    # site-packages, so whether pytest there has the plugin is unknown and `-n` is not risked.
    spec = registry.tools["pytest-xdist"]
    root = tmp_path / "project"
    write(root, "requirements-dev.txt", "pytest\npytest-xdist\n")
    shim = lambda name: str(tmp_path / ".pyenv" / "shims" / name) if name == "pytest" else None  # noqa: E731
    assert detect_tool(spec, scan_project(root), {}, False, which=shim) is None


def test_pytest_xdist_of_another_python_version_does_not_count(tmp_path: Path, registry):
    from susanoo.discovery.executables import python_package_installed

    prefix = tmp_path / "homebrew"
    (prefix / "lib" / "python3.11" / "site-packages" / "pytest_xdist-3.6.1.dist-info").mkdir(parents=True)
    (prefix / "lib" / "python3.11" / "site-packages" / "pytest-8.3.4.dist-info").mkdir()
    (prefix / "lib" / "python3.12" / "site-packages" / "pytest-8.3.4.dist-info").mkdir(parents=True)
    # pytest is in both versions: which one /bin/pytest runs is unknown.
    assert python_package_installed("pytest-xdist", str(prefix / "bin" / "pytest")) is None
    # pytest only in 3.12: that is its environment, and it lacks the plugin.
    (prefix / "lib" / "python3.11" / "site-packages" / "pytest-8.3.4.dist-info").rmdir()
    assert python_package_installed("pytest-xdist", str(prefix / "bin" / "pytest")) is False


def test_debian_dist_packages_layout_is_inspected(tmp_path: Path):
    from susanoo.discovery.executables import python_package_installed

    (tmp_path / "usr" / "lib" / "python3" / "dist-packages" / "pytest_xdist-3.5.0.egg-info").mkdir(
        parents=True
    )
    # Debian: apt packages live in the unversioned /usr/lib/python3/dist-packages.
    assert python_package_installed("pytest-xdist", str(tmp_path / "usr" / "bin" / "pytest")) is True


def test_external_environment_decides_by_declaration_even_with_pytest_on_path(tmp_path: Path, registry):
    # Poetry runs the checks in its own environment, not in the one the PATH pytest belongs to.
    spec = registry.tools["pytest-xdist"]
    root, system = tmp_path / "project", tmp_path / "system"
    write(root, "pyproject.toml", '[tool.poetry.group.dev.dependencies]\npytest = "*"\npytest-xdist = "*"\n')
    _site_packages(system, "pytest-8.3.4.dist-info")
    on_path = lambda name: str(system / "bin" / name) if name == "pytest" else None  # noqa: E731
    assert detect_tool(spec, scan_project(root), {}, False, which=on_path) is None
    finding = detect_tool(spec, scan_project(root), {}, False, which=on_path, external_environment=True)
    assert finding is not None and any("declared" in e for e in finding.evidence)
