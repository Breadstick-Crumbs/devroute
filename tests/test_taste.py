from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from susanoo import taste
from susanoo.adapters.claude_code.global_install import apply_global, plan_global
from susanoo.adapters.claude_code.installer import (
    apply_install,
    apply_uninstall,
    plan_install,
    plan_uninstall,
    taste_skill_template,
)
from susanoo.cli import main
from susanoo.paths import user_config_path
from tests.conftest import write

TASTE_SKILL = ".claude/skills/taste-ui/SKILL.md"


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([*args, "--path", str(root)], stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def no_taste_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUSANOO_TASTE_DIR", raising=False)
    monkeypatch.delenv("SUSANOO_BROWSER", raising=False)


def test_project_install_includes_taste_skill_and_uninstall_removes_it(tmp_path: Path) -> None:
    actions = plan_install(tmp_path)
    apply_install(tmp_path, actions)
    assert (tmp_path / TASTE_SKILL).read_text(encoding="utf-8") == taste_skill_template()
    assert apply_uninstall(tmp_path, plan_uninstall(tmp_path))
    assert not (tmp_path / TASTE_SKILL).exists()


def test_no_taste_option(tmp_path: Path) -> None:
    code, _, _ = run(tmp_path, "init", "--no-taste")
    assert code == 0
    assert not (tmp_path / TASTE_SKILL).exists()
    assert (tmp_path / ".claude/skills/susanoo/SKILL.md").is_file()


def test_user_owned_taste_skill_is_never_overwritten(tmp_path: Path) -> None:
    write(tmp_path, TASTE_SKILL, "my own taste skill")
    actions = {a.path: a.action for a in plan_install(tmp_path)}
    assert actions[TASTE_SKILL] == "conflict"


def test_global_install_includes_taste_skill(isolated_susanoo_home: Path) -> None:
    apply_global(plan_global(auto=True, with_agents=False, force=False), auto=True, with_agents=False)
    assert (isolated_susanoo_home / "claude-config" / "skills" / "taste-ui" / "SKILL.md").is_file()


def test_taste_skill_frontmatter_names_itself_and_visual_check() -> None:
    head = taste_skill_template().split("\n---\n", 1)[0]
    assert "name: taste-ui" in head
    assert "susanoo-capabilities: [visual_check]" in head
    assert "run:" not in head  # the visual check is reported evidence, never a command Susanoo runs


def test_location_precedence(
    monkeypatch: pytest.MonkeyPatch, isolated_susanoo_home: Path, tmp_path: Path
) -> None:
    assert taste.taste_location() == taste.TasteLocation(isolated_susanoo_home / "taste", "default")
    user_config_path().write_text(f'[ui]\ntaste_dir = "{(tmp_path / "mine").as_posix()}"\n', encoding="utf-8")
    assert taste.taste_location() == taste.TasteLocation(tmp_path / "mine", "user config")
    monkeypatch.setenv("SUSANOO_TASTE_DIR", str(tmp_path / "env"))
    assert taste.taste_location() == taste.TasteLocation(tmp_path / "env", "SUSANOO_TASTE_DIR")


def test_project_config_cannot_move_the_taste_folder(isolated_susanoo_home: Path, tmp_path: Path) -> None:
    write(tmp_path, ".susanoo/config.toml", '[ui]\ntaste_dir = "C:/somewhere/else"\n')
    code, out, _ = run(tmp_path, "taste", "path", "--json")
    assert code == 0
    assert json.loads(out)["source"] == "default"


def test_path_reports_profile_and_sites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    profile = tmp_path / "taste"
    write(profile, "TASTE.md", "# Taste\n")
    write(profile, "sites/wispr/tokens.json", "{}")
    (profile / "sites" / "empty").mkdir()
    monkeypatch.setenv("SUSANOO_TASTE_DIR", str(profile))
    code, out, _ = run(tmp_path, "taste", "path", "--json")
    data = json.loads(out)
    assert code == 0 and data["profile"] is True and data["sites"] == ["wispr"]


def test_shoot_without_browser_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(taste, "find_browser", lambda: None)
    code, _, err = run(tmp_path, "taste", "shoot", "index.html", "out")
    assert code != 0 and "SUSANOO_BROWSER" in err


def test_shoot_without_node_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(taste, "find_browser", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(taste, "which_outside_project", lambda _name: None)
    code, _, err = run(tmp_path, "taste", "shoot", "index.html", "out")
    assert code != 0 and "Node.js" in err


def test_shoot_needs_two_arguments(tmp_path: Path) -> None:
    code, _, err = run(tmp_path, "taste", "shoot", "index.html")
    assert code != 0 and "usage" in err


def test_capture_passes_the_browser_and_sites_to_node(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    class Done:
        returncode = 0

    def fake_run(cmd: list[str], env: dict[str, str], check: bool, timeout: int) -> Done:
        calls.append((cmd, env))
        return Done()

    monkeypatch.setenv("SUSANOO_TASTE_DIR", str(tmp_path / "taste"))
    monkeypatch.setattr(taste, "find_browser", lambda: "/opt/chrome")
    monkeypatch.setattr(taste, "which_outside_project", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(taste.subprocess, "run", fake_run)
    code, _, _ = run(tmp_path, "taste", "capture", "wispr=https://wisprflow.ai/")
    assert code == 0
    cmd, env = calls[0]
    assert cmd[1].endswith("capture.mjs") and Path(cmd[1]).is_file()
    assert cmd[2:] == [str(tmp_path / "taste" / "sites"), "wispr=https://wisprflow.ai/"]
    assert env["SUSANOO_BROWSER"] == "/opt/chrome"


@pytest.mark.parametrize(
    "pair", ["wispr", "=https://x.io", "wi spr=https://x.io", "wispr=ftp://x.io", "a/b=https://x.io"]
)
def test_capture_rejects_bad_site_arguments(pair: str) -> None:
    with pytest.raises(taste.TasteError):
        taste.parse_sites([pair])


def test_shipped_scripts_exist() -> None:
    for name in ("shoot.mjs", "capture.mjs", "extract.js"):
        assert (taste.SCRIPTS / name).is_file()


def test_taste_dir_must_be_a_string(tmp_path: Path) -> None:
    from susanoo.config import ConfigError, load_config

    write(tmp_path, ".susanoo/config.toml", "[ui]\ntaste_dir = 3\n")
    with pytest.raises(ConfigError, match="taste_dir"):
        load_config(tmp_path)


# --- review findings (ADR 0010) ---------------------------------------------------------------


def test_planted_executables_in_the_project_are_never_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On Windows `shutil.which` searches the current folder first; a repository must not win."""
    project = tmp_path / "repo"
    for name in ("node.cmd", "node.exe", "node", "msedge.cmd", "msedge.exe", "chromium"):
        write(project, name, "@echo pwned\n")
    (project / "node").chmod(0o755)
    real = tmp_path / "real-bin"
    write(real, "node.exe" if taste.sys.platform == "win32" else "node", "")
    (real / ("node.exe" if taste.sys.platform == "win32" else "node")).chmod(0o755)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(project), str(project / "bin"), str(real)]))
    found = taste.which_outside_project("node")
    assert found is not None and Path(found).parent == real.resolve()
    assert taste.which_outside_project("msedge") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch-file rule")
def test_batch_files_are_refused_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write(tmp_path / "bin", "node.cmd", "@echo hi\n")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert taste.which_outside_project("node") is None


@pytest.mark.parametrize("variable", ["SUSANOO_TASTE_DIR", "SUSANOO_BROWSER"])
def test_relative_paths_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str) -> None:
    monkeypatch.setenv(variable, "relative/path")
    with pytest.raises(taste.TasteError, match="absolute"):
        taste.taste_location() if variable == "SUSANOO_TASTE_DIR" else taste.find_browser()


def test_relative_taste_dir_in_user_config_is_rejected(tmp_path: Path) -> None:
    user_config_path().write_text('[ui]\ntaste_dir = "taste"\n', encoding="utf-8")
    code, _, err = run(tmp_path, "taste", "path")
    assert code != 0 and "absolute" in err


def test_missing_configured_browser_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SUSANOO_BROWSER", str(tmp_path / "nope.exe"))
    with pytest.raises(taste.TasteError, match="does not exist"):
        taste.find_browser()


def test_failing_script_returns_non_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Failed:
        returncode = 1

    monkeypatch.setattr(taste, "find_browser", lambda: "/opt/chrome")
    monkeypatch.setattr(taste, "which_outside_project", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(taste.subprocess, "run", lambda *a, **k: Failed())
    code, _, _ = run(tmp_path, "taste", "shoot", "index.html", "out")
    assert code == 1


def test_script_timeout_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def slow(*_a: object, **_k: object) -> None:
        raise taste.subprocess.TimeoutExpired("node", 1)

    monkeypatch.setattr(taste, "find_browser", lambda: "/opt/chrome")
    monkeypatch.setattr(taste, "which_outside_project", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(taste.subprocess, "run", slow)
    code, _, err = run(tmp_path, "taste", "shoot", "index.html", "out")
    assert code != 0 and "did not finish" in err


@pytest.mark.parametrize("slug", ["café", "con", "NUL", "a" * 65])
def test_unicode_reserved_and_long_slugs_are_rejected(slug: str) -> None:
    with pytest.raises(taste.TasteError):
        taste.parse_sites([f"{slug}=https://x.io"])


def test_installed_skill_turns_visual_check_from_gap_into_manual_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: Path
) -> None:
    from susanoo.config import load_config
    from susanoo.pipeline import plan_task

    monkeypatch.delenv("CLAUDE_CODE_DESKTOP_APP_VERSION", raising=False)
    task = "Add a sortable, filterable orders table to the admin dashboard"
    bare = tmp_path / "bare"
    write(bare, "package.json", {"devDependencies": {}})
    write(bare, "src/App.tsx", "export const App = () => null;\n")
    assert (
        plan_task(task, bare, load_config(), home=home).verification.get("visual_check").mode == "unavailable"
    )
    with_skill = tmp_path / "with-skill"
    write(with_skill, "package.json", {"devDependencies": {}})
    write(with_skill, "src/App.tsx", "export const App = () => null;\n")
    write(with_skill, TASTE_SKILL, taste_skill_template())
    step = plan_task(task, with_skill, load_config(), home=home).verification.get("visual_check")
    assert step.mode == "manual" and step.command is None


def test_global_uninstall_removes_unmodified_taste_skill(isolated_susanoo_home: Path) -> None:
    from susanoo.adapters.claude_code.global_install import apply_global_uninstall, plan_global_uninstall

    apply_global(plan_global(auto=False, with_agents=False, force=False), auto=False, with_agents=False)
    skill = isolated_susanoo_home / "claude-config" / "skills" / "taste-ui" / "SKILL.md"
    assert skill.is_file()
    apply_global_uninstall(plan_global_uninstall())
    assert not skill.exists()


def test_global_no_taste(isolated_susanoo_home: Path) -> None:
    actions = plan_global(auto=False, with_agents=False, force=False, with_taste=False)
    assert not any("taste-ui" in a.path for a in actions)


def test_tools_under_the_home_folder_are_found_when_running_from_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Volta, scoop or nvm put Node under the home folder; running from ~ must still find it."""
    home_dir = tmp_path / "home"
    name = "node.exe" if sys.platform == "win32" else "node"
    write(home_dir / "tools" / "bin", name, "")
    (home_dir / "tools" / "bin" / name).chmod(0o755)
    monkeypatch.setattr(taste.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.chdir(home_dir)
    monkeypatch.setenv("PATH", str(home_dir / "tools" / "bin"))
    assert taste.which_outside_project("node") == str((home_dir / "tools" / "bin" / name).resolve())


def test_motion_probe_ships_and_capture_uses_it() -> None:
    probe = (taste.SCRIPTS / "motion-probe.js").read_text(encoding="utf-8")
    capture = (taste.SCRIPTS / "capture.mjs").read_text(encoding="utf-8")
    assert "window.__susanooMotion" in probe and "motion-probe.js" in capture
    # The mobile menu probe must never click a real link (it would navigate away from the site).
    assert "Never a real link" in probe
    # The Python timeout must outlast the script's own watchdog, so the script can always clean up.
    assert taste.TIMEOUTS["capture.mjs"] > 3500
