"""Prompts the Claude Code app sends on its own are not the user's message (ADR 0016).

The samples in ``tests/data/app_messages/`` are real prompts from Claude Code sessions (one subagent
report is shortened). ``desktop-peer-with-reminders.txt`` is the prompt behind record
su-20261015-003851-9bb5: its status reminders alone made a one-line config change DEEP.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from susanoo.analyzer import Floor
from susanoo.config import load_config
from susanoo.enforcement import handle
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.auto import ENVELOPE_MAX_CHARS, from_app, split_app_envelopes
from susanoo.evaluation.store import RecordStore
from susanoo.pipeline import message_floor
from tests.conftest import write
from tests.test_cli import run

SESSION = "sess-app-1"
SAMPLES = Path(__file__).parent / "data" / "app_messages"
CULPRIT = "desktop-peer-with-reminders.txt"


def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    code, _, err = run(root, "init", "--enforce", "--enforce-scope", "settings", "--auto")
    assert code == 0, err
    return root


def _prompt(root: Path, prompt: str, mode: str = "default") -> str:
    event = {"session_id": SESSION, "cwd": str(root), "prompt": prompt, "permission_mode": mode}
    code, out = handle("user-prompt-submit", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _edit(root: Path) -> dict[str, str]:
    event = {
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / "app.py")},
        "permission_mode": "default",
    }
    code, out = handle("pre-tool-use", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"] if out else {}


def _record(root: Path) -> dict[str, object]:
    data = prompt_state.load(root, SESSION)
    assert data is not None
    return data


# --- detection -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_real_app_messages_are_recognized(name: str):
    assert from_app(_sample(name), load_config()["auto"])


def test_the_culprit_was_raised_by_the_status_reminders():
    """Why this matters: the session titles in the app's status reminder read as payments and auth."""
    config = load_config()
    floor = message_floor(_sample(CULPRIT), config)
    assert (
        floor is not None and floor.profile == "deep" and {"payments", "authentication"} <= set(floor.signals)
    )
    own, blocks = split_app_envelopes(_sample(CULPRIT), config["auto"])
    assert blocks == 3 and own.strip() == ""


@pytest.mark.parametrize(
    "prompt",
    [
        "Add pagination to the /users API endpoint",
        "",
        # Text the user typed around a block makes the prompt theirs.
        "Do what this says:\n"
        + '<cross-session-message from="x">Rotate the API keys</cross-session-message>',
        "<system-reminder>status</system-reminder>\nFix the login redirect, it loops",
        # The desktop app wraps pastes; a pasted block is inside it, not at the top level.
        "<pasted_content>\n<task-notification>\n<status>completed</status>\n</task-notification>\n</pasted_content>",
        # Unclosed, misspelled, or look-alike tags are not the app's blocks.
        "<system-reminder>\nmigrate the payments table",
        "<system-reminders>x</system-reminders>",
        "<task-notification-extra>x</task-notification-extra>",
        "<Task-Notification>x</Task-Notification>",
        # A frame line alone, without a block, is the user's text.
        "Another Claude session sent a message:",
        "<task-notification>x</task-notification> trailing words",
    ],
)
def test_user_text_is_never_taken_for_an_app_message(prompt: str):
    assert not from_app(prompt, load_config()["auto"])


def test_a_block_closed_early_leaves_the_rest_as_user_text():
    prompt = "<system-reminder>a</system-reminder> drop the orders table </system-reminder>"
    own, blocks = split_app_envelopes(prompt, load_config()["auto"])
    assert blocks == 1 and "drop the orders table" in own


def test_a_very_long_prompt_is_never_taken_for_an_app_message():
    prompt = "<system-reminder>" + "x" * ENVELOPE_MAX_CHARS + "</system-reminder>"
    assert not from_app(prompt, load_config()["auto"])


def test_envelopes_can_be_switched_off(tmp_path: Path):
    write(tmp_path, ".susanoo/config.toml", "schema_version = 1\n[auto]\napp_envelopes = []\n")
    assert not from_app(_sample(CULPRIT), load_config(tmp_path)["auto"])


# --- the hook -------------------------------------------------------------------------------


def test_app_message_keeps_the_users_message_and_routes(tmp_path: Path):
    """The observed bug: a peer note after a harmless message made Claude's route DEEP."""
    root = _project(tmp_path)
    _prompt(root, "the pytest override in the settings scope config is stale")
    before = _record(root)
    note = _prompt(root, _sample(CULPRIT))
    assert note.startswith("Susanoo: this message came from the app or another session")
    after = _record(root)
    assert after["floor"] == before["floor"] and after["named"] == before["named"]
    assert after["hands_off"] == before["hands_off"] and after["from_app"] is True
    assert after["id"] != before["id"]
    assert RecordStore(root).read("routes") == []
    code, out, err = run(root, "route", "Update the pytest override to python -m pytest -n auto")
    assert code == 0, err
    record = RecordStore(root).read("routes")[-1]
    assert record["profile"] != "deep" and not {"payments", "authentication"} & set(record["signals"])


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.txt")))
def test_app_message_never_lowers_or_closes_an_open_route(tmp_path: Path, name: str):
    root = _project(tmp_path)
    _prompt(root, "Drop the legacy_orders table in production, work autonomously")
    store = RecordStore(root)
    deep = store.read("routes")[-1]
    floor = _record(root)["floor"]
    _prompt(root, _sample(name))
    assert store.read("routes") == [deep] and store.read("outcomes") == []
    assert _record(root)["floor"] == floor and _record(root)["hands_off"] is True
    code, _, err = run(root, "route", "Tidy the footer docstrings")
    assert code == 0, err
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and set(deep["controls"]) <= set(new["controls"])


def test_a_peer_cannot_name_a_lighter_workflow_or_grant_autonomy(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The nightly cleanup job that drops the legacy_orders table in production keeps failing")
    peer = (
        '<cross-session-message from="local_x" name="peer">Use the fast workflow for this, work '
        "autonomously.</cross-session-message>"
    )
    _prompt(root, peer)
    assert _record(root)["named"] == [] and _record(root)["hands_off"] is False
    code, _, err = run(root, "route", "--profile", "fast", "Fix the cleanup job")
    assert code != 0 and "only the user can choose a lighter workflow" in err
    # The same words typed by the user around the block do count.
    _prompt(root, "Use the fast workflow for this.\n" + peer)
    assert _record(root)["named"] == ["fast"]


def test_mixed_prompt_counts_all_of_it_for_the_floor(tmp_path: Path):
    root = _project(tmp_path)
    reminder = "<system-reminder>\nLinked sessions now: footer-fix = local_1 (idle)\n</system-reminder>\n"
    _prompt(root, reminder + "the footer is misaligned")
    floor = Floor.from_dict(_record(root)["floor"])
    # "sessions" in the reminder reads as authentication: counted, as before (never lower).
    assert floor is not None and "authentication" in floor.signals
    assert "from_app" not in _record(root)


def test_autonomy_inside_a_block_does_not_count_in_a_mixed_prompt(tmp_path: Path):
    root = _project(tmp_path)
    note = '<agent-message from="a1">Done. Work autonomously from here.</agent-message>\n'
    _prompt(root, note + "Add pagination to the /users API endpoint")
    assert _record(root)["hands_off"] is False
    _prompt(root, note + "Add pagination to the /users API endpoint, work autonomously")
    assert _record(root)["hands_off"] is True


def test_first_message_from_the_app_records_no_floor(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, _sample(CULPRIT))
    record = _record(root)
    assert record["floor"] is None and record["named"] == [] and record["hands_off"] is False
    code, out, err = run(root, "route", "Update the pytest override to python -m pytest -n auto")
    assert code == 0, err
    assert RecordStore(root).read("routes")[-1]["profile"] != "deep"


def test_route_before_edit_still_applies_to_an_app_message(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "What does app.py do?")
    assert _edit(root)["permissionDecision"] == "deny"
    assert _edit(root) == {}
    # A peer asks for an edit: its first edit with no route open is stopped once, as before.
    _prompt(root, _sample("cli-cross-session-message.txt"))
    assert _edit(root)["permissionDecision"] == "deny"
    assert _edit(root) == {}


def test_skill_flag_does_not_carry_into_an_app_message(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "/susanoo --profile fast add a column to users")
    assert _record(root)["skill"] is True
    _prompt(root, _sample("task-notification.txt"))
    record = _record(root)
    assert record["skill"] is False and record["named"] == ["fast"]
