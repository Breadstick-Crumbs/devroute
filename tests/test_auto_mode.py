"""Global auto mode: prompt classification, autonomy, auto routing, global install, home records."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from susanoo.adapters.claude_code.settings_hooks import owned_events
from susanoo.cli import main
from susanoo.config import load_config
from susanoo.enforcement import gates, handle
from susanoo.enforcement.auto import AMBIGUOUS, QUESTION, SKIP, TASK, classify, wants_autonomy
from susanoo.enforcement.state import RouteState
from susanoo.evaluation.store import RecordStore
from susanoo.paths import OFF_FILE, find_project_root, global_project_dir, records_dir
from tests.conftest import GATED_TOML, gated_config, write
from tests.test_cli import run

SESSION = "sess-auto-1"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: Path) -> Path:
    claude = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return claude


# --- classification ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt, kind",
    [
        ("Add pagination to the /users API endpoint", TASK),
        ("can you fix the login bug on mobile", TASK),
        ("ok now please refactor the billing module", TASK),
        ("I need to add a column to the users table", TASK),
        ("Make sure the CSV export handles unicode", TASK),
        ("What does this function do?", QUESTION),
        ("Could you explain how routing works", QUESTION),
        ("Review the auth middleware for bugs", QUESTION),
        ("Is it ok to drop the legacy table?", QUESTION),
        ("I want to understand the caching layer", AMBIGUOUS),
        ("The checkout page is broken when the cart is empty", AMBIGUOUS),
        ("fix it", AMBIGUOUS),
        ("?add a column to users", SKIP),
        ("/susanoo add a column to users", SKIP),
        ("!git status", SKIP),
        ("", SKIP),
    ],
)
def test_prompt_classification(prompt: str, kind: str):
    assert classify(prompt, load_config()["auto"]) == kind


def test_autonomy_phrases():
    cfg = load_config()["auto"]
    assert wants_autonomy("Add retries to the webhook handler, work autonomously", cfg)
    assert wants_autonomy("Fix the flaky test, don't ask me anything", cfg)
    assert not wants_autonomy("Add retries to the webhook handler", cfg)
    # A bare "don't ask" counts only where it ends its clause, not as part of a feature.
    assert wants_autonomy("Fix the settings page form validation, don't ask", cfg)
    assert wants_autonomy("Don't ask, just fix the settings page", cfg)
    assert not wants_autonomy("Don't ask for the password twice on the login form", cfg)


# --- autonomy in the gates ----------------------------------------------------------------


def _state(autonomous: bool = False) -> RouteState:
    route = {
        "id": "su-1",
        "profile": "standard",
        "controls": ["irreversible-action-gate"],
        "verification": [],
    }
    if autonomous:
        route["autonomous"] = True
    return RouteState(route=route, approved=False, closed=False, missing=["unit_tests"])


EDIT = {"tool_name": "Edit", "tool_input": {"file_path": "app.py"}}
PUSH = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
APPROVE = {"tool_name": "Bash", "tool_input": {"command": "susanoo approve"}}


def test_hands_off_by_default_in_every_mode(tmp_path: Path):
    # Claude Code's own permission settings decide; only the evidence gate stays.
    config = load_config()
    for mode in ("default", "plan", ""):
        for event in (EDIT, PUSH):
            assert (
                gates.pre_tool_use({**event, "permission_mode": mode}, _state(), tmp_path, config).permission
                is None
            )
        # Approval integrity stays: an approval Claude recorded itself would count later.
        assert (
            gates.pre_tool_use({**APPROVE, "permission_mode": mode}, _state(), tmp_path, config).permission
            == "ask"
        )
    assert gates.stop({"permission_mode": "default"}, _state(), config, 0, work_started=True).block_stop


def test_default_mode_gates_once(tmp_path: Path):
    config = gated_config()
    assert (
        gates.pre_tool_use({**EDIT, "permission_mode": "default"}, _state(), tmp_path, config).permission
        == "deny"
    )


@pytest.mark.parametrize("mode", ["acceptEdits", "auto", "dontAsk", "bypassPermissions"])
def test_autonomous_modes_ask_nothing(tmp_path: Path, mode: str):
    config = load_config()
    for event in (EDIT, PUSH):
        assert (
            gates.pre_tool_use({**event, "permission_mode": mode}, _state(), tmp_path, config).permission
            is None
        )
    assert (
        gates.pre_tool_use({**APPROVE, "permission_mode": mode}, _state(), tmp_path, config).permission
        == "ask"
    )


def test_approved_route_keeps_the_irreversible_gate(tmp_path: Path):
    # Autonomy asked for in words is recorded as approval: no plan gate, irreversible gate kept.
    config = gated_config()
    approved = _state()
    approved.approved = True
    assert (
        gates.pre_tool_use({**EDIT, "permission_mode": "default"}, approved, tmp_path, config).permission
        is None
    )
    assert (
        gates.pre_tool_use({**PUSH, "permission_mode": "default"}, approved, tmp_path, config).permission
        == "ask"
    )


def test_accepting_missing_evidence_is_refused_in_autonomous_mode(tmp_path: Path):
    config = load_config()
    command = {
        "tool_name": "Bash",
        "tool_input": {"command": "susanoo outcome latest --status completed --accept-missing-evidence"},
    }
    decision = gates.pre_tool_use(
        {**command, "permission_mode": "bypassPermissions"}, _state(), tmp_path, config
    )
    assert decision.permission == "deny"


def test_evidence_gate_stays_in_autonomous_mode():
    config = load_config()
    decision = gates.stop({"permission_mode": "bypassPermissions"}, _state(), config, 0, work_started=True)
    assert decision.block_stop


# --- auto routing through the hook ----------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    return root


def _prompt(root: Path, prompt: str, mode: str = "default") -> str:
    event = {"session_id": SESSION, "cwd": str(root), "prompt": prompt, "permission_mode": mode}
    code, out = handle("user-prompt-submit", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _auto_project(tmp_path: Path, gated: bool = False) -> Path:
    root = _project(tmp_path)
    code, _, err = run(root, "init", "--enforce", "--enforce-scope", "settings", "--auto")
    assert code == 0, err
    if gated:
        with (root / ".susanoo" / "config.toml").open("a", encoding="utf-8") as fh:
            fh.write(GATED_TOML)
    return root


def test_project_auto_routes_tasks_and_ignores_questions(tmp_path: Path):
    root = _auto_project(tmp_path)
    assert "UserPromptSubmit" in owned_events(json.loads((root / ".claude/settings.local.json").read_text()))
    note = _prompt(root, "What does app.py do?")
    assert note.startswith("Susanoo did not route this message") and "susanoo route" in note
    assert _prompt(root, "?add a column to the users table") == ""
    assert RecordStore(root).read("routes") == []
    context = _prompt(root, "Add pagination to the /users API endpoint")
    assert "Susanoo routed this task automatically" in context and "STANDARD" in context
    assert "Do not wait for approval" in context and "wait for the user's approval" not in context
    assert "ask the user instead of guessing" in context
    routes = RecordStore(root).read("routes")
    assert len(routes) == 1 and routes[0]["session_id"] == SESSION and routes[0]["auto"] is True


def test_gated_project_waits_for_plan_approval(tmp_path: Path):
    root = _auto_project(tmp_path / "gated", gated=True)
    context = _prompt(root, "Add pagination to the /users API endpoint")
    assert "wait for the user's approval" in context


def test_follow_ups_continue_and_riskier_tasks_escalate(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint")
    assert "continues the task in progress" in _prompt(root, "Add a page size option to the pagination")
    assert len(RecordStore(root).read("routes")) == 1
    context = _prompt(root, "Add Stripe billing with refunds to the users endpoint")
    assert "DEEP" in context
    assert len(RecordStore(root).read("routes")) == 2


def test_autonomy_request_routes_hands_off(tmp_path: Path):
    root = _auto_project(tmp_path)
    context = _prompt(root, "Add pagination to the /users API endpoint, work autonomously")
    assert "Do not wait for approval" in context
    assert "instead of guessing" not in context  # the user asked not to be asked
    assert RecordStore(root).read("routes")[-1]["autonomous"] is True
    event = {**EDIT, "session_id": SESSION, "cwd": str(root), "permission_mode": "default"}
    event["tool_input"] = {"file_path": str(root / "app.py")}
    assert handle("pre-tool-use", event)[1] == ""


def test_low_confidence_prompt_gets_questions_through_the_hook(tmp_path: Path):
    root = _auto_project(tmp_path)
    context = _prompt(root, "can you improve the thing")
    assert "Low confidence in this route:" in context and "focused questions" in context


def test_mid_task_autonomy_stops_the_questions(tmp_path: Path):
    root = _auto_project(tmp_path)
    assert "instead of guessing" in _prompt(root, "Add pagination to the /users API endpoint")
    note = _prompt(root, "also add a page size limit, work autonomously")
    assert "do not stop to ask clarifying questions" in note


def test_autonomous_permission_mode_brief_does_not_wait(tmp_path: Path):
    root = _auto_project(tmp_path)
    assert "Do not wait for approval" in _prompt(
        root, "Add pagination to the /users API endpoint", "bypassPermissions"
    )


def test_without_auto_nothing_is_routed(tmp_path: Path):
    root = _project(tmp_path)
    run(root, "init", "--enforce", "--enforce-scope", "settings")
    assert _prompt(root, "Add pagination to the /users API endpoint") == ""


# --- global install -----------------------------------------------------------------------------


def _global(*extra: str) -> tuple[int, str, str]:
    return run(Path.cwd(), "init", "--global", *extra)


def test_global_install_merges_and_uninstall_restores(isolated: Path):
    foreign = {"matcher": "Grep", "hooks": [{"type": "command", "command": "my-tool --check"}]}
    write(isolated, "settings.json", {"model": "opus", "hooks": {"PreToolUse": [foreign]}})
    write(isolated, "CLAUDE.md", "# Mine\n\nKeep my notes.\n")
    code, out, err = _global()
    assert code == 0, err
    settings = json.loads((isolated / "settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "opus" and settings["hooks"]["PreToolUse"][0] == foreign
    assert owned_events(settings) == {"PreToolUse", "Stop", "SessionStart", "UserPromptSubmit"}
    command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert command.endswith("-P -m susanoo hook --global user-prompt-submit")
    playbook = (isolated / "CLAUDE.md").read_text(encoding="utf-8")
    assert playbook.startswith("# Mine\n\nKeep my notes.") and "<!-- susanoo:begin" in playbook
    assert (isolated / "skills/susanoo/SKILL.md").is_file() and (
        isolated / "agents/susanoo-reviewer.md"
    ).is_file()
    # A second run changes nothing.
    _, out, _ = _global("--json")
    assert {a["action"] for a in json.loads(out)["actions"]} == {"unchanged"}
    code, _, err = _global("--uninstall")
    assert code == 0, err
    settings = json.loads((isolated / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"model": "opus", "hooks": {"PreToolUse": [foreign]}}
    assert (isolated / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n\nKeep my notes.\n"
    assert not (isolated / "skills/susanoo").exists()


def test_new_playbook_gets_rules_and_is_removed_with_uninstall(isolated: Path):
    _global()
    playbook = (isolated / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## My rules" in playbook and "## Workflow: Susanoo" in playbook
    _global("--uninstall")
    assert not (isolated / "CLAUDE.md").exists()
    assert not (isolated / "settings.json").exists()


def test_playbook_block_is_updated_in_place(isolated: Path):
    _global()
    path = isolated / "CLAUDE.md"
    edited = path.read_text(encoding="utf-8").replace("## My rules", "## My rules\n\n- Use tabs.")
    edited = edited.replace("## Workflow: Susanoo", "## Workflow: OLD")
    path.write_text(edited, encoding="utf-8")
    _global()
    playbook = path.read_text(encoding="utf-8")
    assert "- Use tabs." in playbook and "## Workflow: Susanoo" in playbook and "OLD" not in playbook
    assert playbook.count("<!-- susanoo:begin") == 1


def test_global_records_live_in_home_and_respect_opt_out(isolated: Path, tmp_path: Path):
    _global()
    root = _project(tmp_path)
    context = _prompt(root, "Add pagination to the /users API endpoint")
    assert "Susanoo routed this task" in context
    assert not (root / ".susanoo").exists()
    assert records_dir(root) == global_project_dir(root) / "records"
    assert json.loads((global_project_dir(root) / "project.json").read_text())["path"] == str(root.resolve())
    (root / OFF_FILE).write_text("", encoding="utf-8")
    assert _prompt(root, "Add a dark mode toggle to the settings page") == ""


def test_route_before_edit_respects_the_global_opt_out(isolated: Path, tmp_path: Path):
    _global()
    root = _project(tmp_path)
    edit = {
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / "app.py")},
        "permission_mode": "default",
    }
    _prompt(root, "The footer is misaligned")
    assert json.loads(handle("pre-tool-use", edit)[1])["hookSpecificOutput"]["permissionDecision"] == "deny"
    # A new message is saved, then the project opts out before its first edit.
    _prompt(root, "The header is misaligned too")
    (root / OFF_FILE).write_text("", encoding="utf-8")
    assert handle("pre-tool-use", edit) == (0, "")


def test_home_directory_is_never_a_project(isolated: Path, home: Path):
    _global()
    assert _prompt(home, "Add pagination to the /users API endpoint") == ""
    notes = home / "notes"
    notes.mkdir()
    assert find_project_root(notes) == notes.resolve()


def test_user_config_applies_everywhere(isolated_susanoo_home: Path):
    write(isolated_susanoo_home, "config.toml", "schema_version = 1\n[auto]\nmin_words = 1\n")
    assert classify("deploy now", load_config()["auto"]) == TASK


def test_doctor_reports_global_mode(isolated: Path, tmp_path: Path):
    _global()
    root = _project(tmp_path)
    _, out, _ = run(root, "doctor", "--json")
    check = next(c for c in json.loads(out)["checks"] if c["check"] == "global install")
    assert "on for this project" in check["detail"] and "routed automatically" in check["detail"]


# --- review regressions ---------------------------------------------------------------------------


def test_global_hooks_never_import_project_code(isolated: Path, tmp_path: Path):
    import subprocess
    import sys

    _global()
    root = _project(tmp_path)
    write(root, "json.py", "print('PWNED')\n")
    write(root, "re.py", "print('PWNED')\n")
    event = json.dumps({"session_id": SESSION, "cwd": str(root), "prompt": "what is this?"})
    result = subprocess.run(
        [sys.executable, "-P", "-m", "susanoo", "hook", "--global", "user-prompt-submit"],
        cwd=root, input=event, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert "PWNED" not in result.stdout + result.stderr
    settings = json.loads((isolated / "settings.json").read_text(encoding="utf-8"))
    assert all(
        " -P -m susanoo hook --global " in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]
    )


@pytest.mark.parametrize(
    "text",
    [
        "Mine A\n<!-- susanoo:begin (managed) -->\nold\nMY RULES B\n",  # end marker deleted
        "<!-- susanoo:begin -->\none\n<!-- susanoo:end -->\n<!-- susanoo:begin -->\ntwo\n<!-- susanoo:end -->\n",
        "Mine\n<!-- susanoo:end -->\nlater\n<!-- susanoo:begin -->\n",
    ],
)
def test_damaged_playbook_markers_leave_the_file_alone(isolated: Path, text: str):
    write(isolated, "CLAUDE.md", text)
    _, out, _ = _global("--json")
    assert ("CLAUDE.md", "conflict") in [(a["path"], a["action"]) for a in json.loads(out)["actions"]]
    assert (isolated / "CLAUDE.md").read_text(encoding="utf-8") == text
    _global("--uninstall")
    assert (isolated / "CLAUDE.md").read_text(encoding="utf-8") == text


def test_markers_mentioned_in_prose_are_not_blocks(isolated: Path):
    text = "Notes: Susanoo writes `<!-- susanoo:begin -->` blocks.\nKeep this.\n"
    write(isolated, "CLAUDE.md", text)
    _global()
    playbook = (isolated / "CLAUDE.md").read_text(encoding="utf-8")
    assert playbook.startswith(text.rstrip("\n")) and "## Workflow: Susanoo" in playbook
    _global("--uninstall")
    assert (isolated / "CLAUDE.md").read_text(encoding="utf-8") == text


def test_playbook_changes_keep_a_backup_and_line_endings(isolated: Path):
    (isolated).mkdir(parents=True, exist_ok=True)
    (isolated / "CLAUDE.md").write_bytes(b"# Mine\r\n\r\nKeep.\r\n")
    _global()
    raw = (isolated / "CLAUDE.md").read_bytes()
    assert b"<!-- susanoo:begin" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert list(isolated.glob("CLAUDE.md.susanoo-backup-*"))
    _global("--uninstall")
    assert (isolated / "CLAUDE.md").read_bytes() == b"# Mine\r\n\r\nKeep.\r\n"


def test_symlinked_playbook_is_not_replaced(isolated: Path, tmp_path: Path):
    import os

    target = tmp_path / "dotfiles.md"
    target.write_text("# dotfiles\n", encoding="utf-8")
    isolated.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, isolated / "CLAUDE.md")
    except OSError:
        pytest.skip("creating symbolic links is not permitted here")
    _global()
    assert (isolated / "CLAUDE.md").is_symlink() and target.read_text(encoding="utf-8") == "# dotfiles\n"


def test_project_with_own_hooks_wins_over_global(isolated: Path, tmp_path: Path):
    _global()
    root = _project(tmp_path)
    code, _, err = run(root, "init", "--enforce", "--enforce-scope", "settings", "--auto")
    assert code == 0, err  # the global install no longer blocks project setup
    event = json.dumps(
        {"session_id": SESSION, "cwd": str(root), "prompt": "Add pagination to the users endpoint"}
    )
    out = io.StringIO()
    main(
        ["hook", "--global", "user-prompt-submit"], stdin=io.StringIO(event), stdout=out, stderr=io.StringIO()
    )
    assert out.getvalue() == ""  # global steps aside; the project hook routes once
    assert _prompt(root, "Add pagination to the users endpoint") != ""
    assert len(RecordStore(root).read("routes")) == 1


def test_global_mode_needs_a_git_repository(isolated: Path, tmp_path: Path):
    _global()
    folder = tmp_path / "scratch"
    write(folder, "notes.txt", "x")
    assert _prompt(folder, "Write a python script that renames my photos by date") == ""


@pytest.mark.parametrize(
    "prompt",
    [
        "make sense of this stack trace for me",
        "write me a haiku about rust",
        "update me on what you changed",
        "drop it, let's move on to something else",
        "move on to the next item",
        "port 8080 is already in use, what should I do?",
        "seed data looks wrong, why?",
        "support for windows - is it there?",
        "can you make sure you understand the codebase before we start",
    ],
)
def test_conversation_is_not_routed(prompt: str):
    assert classify(prompt, load_config()["auto"]) != TASK


def test_mid_task_autonomy_request_is_honored(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint")
    _prompt(root, "Now add a page size option too, work autonomously")
    approvals = RecordStore(root).read("approvals")
    assert approvals and approvals[-1]["checkpoint"] == "autonomy"
    event = {**EDIT, "session_id": SESSION, "cwd": str(root), "permission_mode": "default"}
    event["tool_input"] = {"file_path": str(root / "app.py")}
    assert handle("pre-tool-use", event)[1] == ""


def test_unrelated_task_after_a_while_gets_its_own_route(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint")
    store = RecordStore(root)
    routes = store.read("routes")
    routes[0]["created_at"] = "2020-01-01T00:00:00+00:00"
    store.path("routes").write_text(json.dumps(routes[0]) + "\n", encoding="utf-8")
    assert "Susanoo routed this task" in _prompt(root, "Add a dark mode toggle to the settings page")
    outcomes = store.read("outcomes")
    assert outcomes and outcomes[0]["status"] == "abandoned" and outcomes[0]["record_id"] == routes[0]["id"]


def test_task_text_is_redacted_in_records(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add retries to the API client; the token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in RecordStore(root).read("routes")[0]["task"]


def test_uninstall_cleans_up_after_susanoo_home_is_deleted(isolated: Path, isolated_susanoo_home: Path):
    import shutil

    _global()
    shutil.rmtree(isolated_susanoo_home)
    _global("--uninstall")
    assert (
        not (isolated / "settings.json").exists() or "susanoo" not in (isolated / "settings.json").read_text()
    )


def test_broken_user_config_does_not_break_hooks(isolated_susanoo_home: Path, tmp_path: Path):
    import subprocess
    import sys

    root = _auto_project(tmp_path)
    write(isolated_susanoo_home, "config.toml", "this is = = not toml")
    event = json.dumps(
        {"session_id": SESSION, "cwd": str(root), "prompt": "Add pagination to the users endpoint"}
    )
    result = subprocess.run(
        [sys.executable, "-P", "-m", "susanoo", "hook", "user-prompt-submit"],
        cwd=root, input=event, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0 and "configuration error" in result.stderr


# --- second review round ---------------------------------------------------------------------


def _age_route(store: RecordStore, minutes_ago: int) -> dict:
    from datetime import UTC, datetime, timedelta

    routes = store.read("routes")
    routes[-1]["created_at"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(
        timespec="seconds"
    )
    store.path("routes").write_text("".join(json.dumps(r) + "\n" for r in routes), encoding="utf-8")
    return routes[-1]


def test_long_running_task_is_not_cut_off(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint, work autonomously")
    store = RecordStore(root)
    _age_route(store, 25)
    # Recent activity on the task (a tool call) keeps later prompts as follow-ups.
    event = {**EDIT, "session_id": SESSION, "cwd": str(root), "permission_mode": "default"}
    event["tool_input"] = {"file_path": str(root / "app.py")}
    handle("pre-tool-use", event)
    assert "continues the task in progress" in _prompt(root, "Add a page size option to the pagination")
    assert not store.read("outcomes")


def test_unfinished_work_is_never_closed_automatically(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint, work autonomously")
    event = {**EDIT, "session_id": SESSION, "cwd": str(root), "permission_mode": "default"}
    event["tool_input"] = {"file_path": str(root / "app.py")}
    handle("pre-tool-use", event)  # work started; the required checks have no evidence yet
    store = RecordStore(root)
    _age_route(store, 120)
    hooks_dir = records_dir(root) / "hooks"
    for path in hooks_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["last_seen"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(data), encoding="utf-8")
    context = _prompt(root, "Add a dark mode toggle to the settings page")
    assert "continues the task in progress" in context and "finish the open one" in context
    assert not store.read("outcomes")


@pytest.mark.parametrize(
    "prompt",
    [
        "Implement retries in the worker, and don't stop on the first error",
        "Add a CLI flag --yes: do not ask for confirmation",
        "Add an option so the bot works on its own without asking, and log it",
    ],
)
def test_feature_descriptions_do_not_grant_autonomy(prompt: str):
    assert not wants_autonomy(prompt, load_config()["auto"])


def test_multiword_exclusions_after_the_verb():
    assert classify("make a list of the files that import requests", load_config()["auto"]) != TASK


def test_path_cache_never_remembers_a_missing_tool(tmp_path: Path):
    from susanoo.discovery import executables

    cache = tmp_path / "cache.json"
    executables.use_path_cache(cache)
    try:
        assert executables._which("definitely-not-a-real-tool-xyz") is None
        executables.save_path_cache()
    finally:
        executables._cache = None
    assert not cache.exists() or "definitely-not-a-real-tool-xyz" not in cache.read_text(encoding="utf-8")


def test_global_install_backs_up_settings_once(isolated: Path):
    write(isolated, "settings.json", {"model": "opus"})
    _global()
    backups = list(isolated.glob("settings.json.susanoo-backup-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == {"model": "opus"}
    _global("--no-auto")
    assert len(list(isolated.glob("settings.json.susanoo-backup-*"))) == 1


def test_doctor_explains_project_setups_in_global_mode(isolated: Path, tmp_path: Path):
    _global()
    settings_scope = _project(tmp_path)
    run(settings_scope, "init", "--enforce", "--enforce-scope", "settings")
    _, out, _ = run(settings_scope, "doctor", "--json")
    detail = next(c for c in json.loads(out)["checks"] if c["check"] == "global install")["detail"]
    assert "step aside" in detail
    skill_scope = tmp_path / "other"
    write(skill_scope, "app.py", "x = 1\n")
    (skill_scope / ".git").mkdir()
    run(skill_scope, "init", "--enforce")
    _, out, _ = run(skill_scope, "doctor", "--json")
    assert any(
        c["status"] == "warn" and "run twice" in c["detail"]
        for c in json.loads(out)["checks"]
        if c["check"] == "global install"
    )


def test_install_prompts_are_tasks():
    assert classify("Install date-fns and use it for the invoice dates", load_config()["auto"]) == TASK


def test_compact_brief_names_installed_extensions_and_gap_hints(tmp_path: Path, home: Path):
    from susanoo.enforcement.auto import compact_brief
    from susanoo.pipeline import plan_task

    root = _project(tmp_path)
    write(
        home,
        ".claude/skills/code-graph/SKILL.md",
        "---\nname: code-graph\ndescription: Code graph for callers and the call chain.\n---\nBody\n",
    )
    plan = plan_task(
        "Add Stripe checkout and store payment status in postgres", root, load_config(), home=home
    )
    brief = compact_brief(plan, "su-1", True, "susanoo")
    assert "Installed tools that fit:" in brief and "code-graph (skill, user)" in brief
    assert "Payment sandbox (consider Stripe CLI (cli), install: https://docs.stripe.com/stripe-cli)" in brief
    assert "Never install anything without the user's approval" in brief


def test_compact_brief_asks_about_the_task_when_confidence_is_low(tmp_path: Path, home: Path):
    from susanoo.enforcement.auto import compact_brief
    from susanoo.pipeline import plan_task

    root = _project(tmp_path)
    vague = plan_task("make it better", root, load_config(), home=home)
    assert vague.assessment.confidence == "low"
    brief = compact_brief(vague, "su-1", True, "susanoo")
    assert "Low confidence in this route:" in brief and "focused questions about scope" in brief
    assert "Do not wait for approval" in brief  # questions about the task, not an approval request
    # The user asked in words not to be asked: no prompt to ask, also not for low confidence.
    quiet = compact_brief(vague, "su-1", True, "susanoo", asked_autonomy=True)
    assert "Low confidence in this route:" not in quiet and "instead of guessing" not in quiet
    gated = compact_brief(vague, "su-1", False, "susanoo")
    assert "Low confidence in this route:" in gated and "wait for the user's approval" in gated
    assert "questions about anything ambiguous" in gated
    gated_quiet = compact_brief(vague, "su-1", False, "susanoo", asked_autonomy=True)
    assert "questions about anything ambiguous" not in gated_quiet
    clear = plan_task("Add pagination to the /users API endpoint", root, load_config(), home=home)
    assert "Low confidence in this route:" not in compact_brief(clear, "su-1", True, "susanoo")


@pytest.mark.parametrize("prompt", ["Install docker on my machine", "install the project dependencies"])
def test_environment_chores_are_not_routed(prompt: str):
    assert classify(prompt, load_config()["auto"]) != TASK


def test_route_summary_follows_hands_off():
    state = _state()
    assert "Hands-off: no plan approval needed" in gates.summary(state, hands_off=True)
    assert "wait for approval" in gates.summary(state)
    compact = {"source": "compact", "permission_mode": "default"}
    assert "wait for approval" not in str(gates.session_start(compact, state, load_config()).context)
    assert "wait for approval" in str(gates.session_start(compact, state, gated_config()).context)


def test_follow_up_under_hands_off_does_not_wait_for_approval(tmp_path: Path):
    root = _auto_project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint")
    follow_up = _prompt(root, "Add a page size option to the pagination")
    assert "continues the task in progress" in follow_up
    assert "wait for approval" not in follow_up and "Hands-off: no plan approval needed" in follow_up


def test_accepting_missing_evidence_is_refused_under_hands_off_in_default_mode(tmp_path: Path):
    command = {
        "tool_name": "Bash",
        "tool_input": {"command": "susanoo outcome latest --status completed --accept-missing-evidence"},
        "permission_mode": "default",
    }
    decision = gates.pre_tool_use(command, _state(), tmp_path, load_config())
    assert decision.permission == "deny" and "hands-off" in decision.reason
    assert gates.pre_tool_use(command, _state(), tmp_path, gated_config()).permission == "ask"
