"""`susanoo init --enforce --enforce-scope settings`: hooks in .claude/settings.local.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from susanoo.adapters.claude_code.installer import skill_template
from susanoo.adapters.claude_code.settings_hooks import owned_events, settings_hooks
from susanoo.enforcement import HOOK_EVENTS
from tests.conftest import write
from tests.test_cli import run

LOCAL = ".claude/settings.local.json"
SKILL = ".claude/skills/susanoo/SKILL.md"
ALL_EVENTS = {"PreToolUse", "Stop", "SessionStart"}
SETTINGS = ("init", "--enforce", "--enforce-scope", "settings")
SNIPPET = {
    "hooks": {
        "SessionStart": [
            {"matcher": "compact", "hooks": [{"type": "command", "command": "susanoo hook session-start"}]}
        ]
    }
}


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def settings(root: Path) -> dict:
    return json.loads((root / LOCAL).read_text(encoding="utf-8"))


def manifest(root: Path) -> dict:
    return json.loads((root / ".susanoo/install.json").read_text(encoding="utf-8"))


def actions_of(out: str) -> dict[str, str]:
    return {a["path"]: a["action"] for a in json.loads(out)["actions"]}


def enforcement_checks(root: Path) -> list[dict]:
    _, out, _ = run(root, "doctor", "--json")
    return [c for c in json.loads(out)["checks"] if c["check"] == "enforcement"]


def test_settings_scope_puts_hooks_in_local_settings_only(tmp_path: Path):
    code, _, _ = run(tmp_path, *SETTINGS)
    assert code == 0
    assert owned_events(settings(tmp_path)) == ALL_EVENTS
    # The skill carries no hooks, so nothing runs twice.
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == skill_template(False)
    assert manifest(tmp_path)["enforce_scope"] == "settings"
    # The choice is kept, and a second run changes nothing.
    _, out, _ = run(tmp_path, "init", "--json")
    assert actions_of(out)[LOCAL] == "unchanged" and actions_of(out)[SKILL] == "unchanged"


def test_existing_settings_are_preserved(tmp_path: Path):
    user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    original = {"permissions": {"allow": ["Bash(ls)"]}, "note": "café", "hooks": {"PreToolUse": [user_hook]}}
    write(tmp_path, LOCAL, original)
    run(tmp_path, *SETTINGS)
    data = settings(tmp_path)
    assert data["permissions"] == original["permissions"] and data["hooks"]["PreToolUse"][0] == user_hook
    assert owned_events(data) == ALL_EVENTS
    assert "café" in (tmp_path / LOCAL).read_text(encoding="utf-8")  # not escaped
    # Turning enforcement off removes only Susanoo's handlers and keeps the user's file.
    run(tmp_path, "init", "--no-enforce")
    assert settings(tmp_path) == original


def test_user_group_after_susanoo_settles_after_one_run(tmp_path: Path):
    run(tmp_path, *SETTINGS)
    data = settings(tmp_path)
    data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo after"}]})
    write(tmp_path, LOCAL, data)
    run(tmp_path, "init")
    _, out, _ = run(tmp_path, "init", "--json")
    assert actions_of(out)[LOCAL] == "unchanged"
    assert any(h["command"] == "echo after" for g in settings(tmp_path)["hooks"]["Stop"] for h in g["hooks"])


def test_switching_back_to_skill_scope_keeps_only_the_compaction_hook(tmp_path: Path):
    run(tmp_path, *SETTINGS)
    run(tmp_path, "init", "--enforce-scope", "skill")
    # The skill cannot provide compaction recovery, and SessionStart cannot run twice.
    assert owned_events(settings(tmp_path)) == {"SessionStart"}
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == skill_template(True)
    assert manifest(tmp_path)["enforce_scope"] == "skill"
    # Turning enforcement off removes it too, and the file Susanoo created with it.
    run(tmp_path, "init", "--no-enforce")
    assert not (tmp_path / LOCAL).exists()


def test_hand_added_snippet_survives_a_scope_round_trip(tmp_path: Path):
    write(tmp_path, LOCAL, SNIPPET)
    run(tmp_path, "init", "--enforce")
    run(tmp_path, "init", "--enforce-scope", "settings")
    run(tmp_path, "init", "--enforce-scope", "skill")
    assert owned_events(settings(tmp_path)) == {"SessionStart"}


def test_skill_scope_never_touches_user_settings(tmp_path: Path):
    write(tmp_path, LOCAL, SNIPPET)
    run(tmp_path, "init", "--enforce")
    run(tmp_path, "init", "--no-enforce")
    run(tmp_path, "init", "--uninstall")
    assert settings(tmp_path) == SNIPPET


def test_user_content_added_to_a_susanoo_created_file_is_kept(tmp_path: Path):
    run(tmp_path, *SETTINGS)
    data = settings(tmp_path)
    data["permissions"] = {"allow": ["Bash(ls)"]}
    write(tmp_path, LOCAL, data)
    run(tmp_path, "init", "--no-enforce")
    assert settings(tmp_path) == {"permissions": {"allow": ["Bash(ls)"]}}


@pytest.mark.parametrize("bad", ["{ not json", '{"hooks": {"PreToolUse": {"matcher": "x"}}}',
                                 '{"hooks": {"PreToolUse": "ab"}}', '{"hooks": {"PreToolUse": null}}',
                                 '{"hooks": {"Stop": ["not a group"]}}', "[1, 2]"])  # fmt: skip
def test_unexpected_settings_stop_before_any_change(tmp_path: Path, bad: str):
    run(tmp_path, "init")
    write(tmp_path, LOCAL, bad)
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "Nothing was changed" in err and "internal error" not in err
    assert (tmp_path / LOCAL).read_text(encoding="utf-8") == bad
    assert manifest(tmp_path).get("enforce") is False


def test_settings_with_a_byte_order_mark_are_handled(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / LOCAL).write_bytes(b"\xef\xbb\xbf" + json.dumps({"permissions": {}}).encode())
    run(tmp_path, *SETTINGS)
    assert owned_events(settings(tmp_path)) == ALL_EVENTS
    run(tmp_path, "init", "--enforce-scope", "skill")
    assert owned_events(settings(tmp_path)) == {"SessionStart"}


def test_modified_skill_with_hooks_blocks_settings_scope(tmp_path: Path):
    run(tmp_path, "init", "--enforce")
    skill = tmp_path / SKILL
    skill.write_text(skill.read_text(encoding="utf-8") + "\nmy notes\n", encoding="utf-8")
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "still declares the enforcement hooks" in err
    assert not (tmp_path / LOCAL).exists()
    # --force replaces the skill (with a backup), so the hooks end up in one place.
    assert run(tmp_path, *SETTINGS, "--force")[0] == 0
    assert "susanoo hook" not in skill.read_text(encoding="utf-8")


def test_skill_scope_refuses_hooks_already_in_settings(tmp_path: Path):
    write(tmp_path, LOCAL, {"hooks": settings_hooks()})  # added by hand, not managed by Susanoo
    code, _, err = run(tmp_path, "init", "--enforce")
    assert code == 1 and "would run twice" in err
    assert not (tmp_path / SKILL).exists()


def test_hooks_in_other_settings_files_block_enforcement(tmp_path: Path, home: Path):
    write(home, ".claude/settings.json", {"hooks": settings_hooks()})
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "settings.json already runs Susanoo's hooks" in err
    assert not (tmp_path / LOCAL).exists()
    assert run(tmp_path, "init", "--no-enforce")[0] == 0


def test_uninstall_removes_settings_hooks(tmp_path: Path):
    write(tmp_path, LOCAL, {"permissions": {}})
    run(tmp_path, *SETTINGS)
    _, out, _ = run(tmp_path, "init", "--uninstall", "--json")
    assert actions_of(out)[LOCAL] == "remove-hooks"
    assert settings(tmp_path) == {"permissions": {}}


def test_uninstall_deletes_a_susanoo_created_file_and_empty_claude_dir(tmp_path: Path):
    run(tmp_path, *SETTINGS, "--no-agents")
    _, out, _ = run(tmp_path, "init", "--uninstall", "--json")
    assert actions_of(out)[LOCAL] == "remove"
    assert not (tmp_path / ".claude").exists()


def test_dry_run_writes_no_settings(tmp_path: Path):
    _, out, _ = run(tmp_path, *SETTINGS, "--dry-run", "--json")
    assert actions_of(out)[LOCAL] == "create"
    assert not (tmp_path / LOCAL).exists()


def test_scope_without_enforce_is_saved_for_later(tmp_path: Path):
    _, _, err = run(tmp_path, "init", "--enforce-scope", "settings")
    assert "takes effect with --enforce" in err
    assert not (tmp_path / LOCAL).exists()
    run(tmp_path, "init", "--enforce")
    assert owned_events(settings(tmp_path)) == ALL_EVENTS


def test_symlinked_settings_are_not_replaced(tmp_path: Path):
    target = tmp_path / "dotfiles.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    try:
        os.symlink(target, tmp_path / LOCAL)
    except OSError:
        pytest.skip("creating symbolic links is not permitted here")
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "symbolic link" in err
    assert (tmp_path / LOCAL).is_symlink()


def test_doctor_reports_scope_duplicates_and_missing_hooks(tmp_path: Path):
    run(tmp_path, *SETTINGS)
    assert any(
        c["status"] == "ok" and "settings.local.json" in c["detail"] for c in enforcement_checks(tmp_path)
    )
    # Hooks added to the skill by hand as well: every gate would run twice.
    write(tmp_path, SKILL, skill_template(True))
    assert any(c["status"] == "warn" and "runs twice" in c["detail"] for c in enforcement_checks(tmp_path))
    # Settings scope recorded, but the hooks were removed by hand.
    write(tmp_path, SKILL, skill_template(False))
    write(tmp_path, LOCAL, {})
    assert any(c["status"] == "warn" and "lacks" in c["detail"] for c in enforcement_checks(tmp_path))


def test_settings_hooks_cover_the_skill_events_and_known_commands():
    hooks = settings_hooks()
    assert set(hooks) == ALL_EVENTS
    commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    assert all(c.split()[:2] == ["susanoo", "hook"] and c.split()[2] in HOOK_EVENTS for c in commands)
    skill = skill_template(True)
    for command in ("susanoo hook pre-tool-use", "susanoo hook stop"):
        assert command in skill and command in commands


@pytest.mark.parametrize("command", [("init", "--no-enforce"), ("init", "--uninstall")])
def test_unreadable_managed_settings_are_never_skipped_silently(tmp_path: Path, command: tuple[str, ...]):
    run(tmp_path, *SETTINGS)
    broken = (tmp_path / LOCAL).read_text(encoding="utf-8").replace("}", "},", 1)
    write(tmp_path, LOCAL, broken)
    code, _, err = run(tmp_path, *command)
    assert code == 1 and "remove the `susanoo hook` entries yourself" in err
    assert manifest(tmp_path)["settings_managed"] is True and manifest(tmp_path)["enforce"] is True


def test_project_in_home_directory_is_not_a_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    write(tmp_path, ".claude/settings.json", {"hooks": settings_hooks()})
    checks = enforcement_checks(tmp_path)
    assert not any("twice" in c["detail"] for c in checks)
    assert any(c["status"] == "ok" for c in checks)


def test_unreadable_skill_is_reported_not_crashed(tmp_path: Path):
    (tmp_path / SKILL).mkdir(parents=True)
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "not a readable text file" in err and "internal error" not in err


def test_symlinked_settings_are_still_detected(tmp_path: Path, home: Path):
    target = tmp_path / "dotfiles" / "settings.json"
    write(tmp_path, "dotfiles/settings.json", {"hooks": settings_hooks()})
    (home / ".claude").mkdir()
    try:
        os.symlink(target, home / ".claude" / "settings.json")
    except OSError:
        pytest.skip("creating symbolic links is not permitted here")
    code, _, err = run(tmp_path, *SETTINGS)
    assert code == 1 and "already runs Susanoo's hooks" in err
