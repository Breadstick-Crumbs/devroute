"""`susanoo lead on|off`: a lead session's prompts are recorded but not routed (ADR 0015)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from susanoo.enforcement import handle
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.prompt import AMBIGUOUS
from susanoo.evaluation.store import RecordStore
from tests.conftest import write
from tests.test_cli import run

LEAD = "sess-lead-1"
WORKER = "sess-worker-1"
TASK = "Add pagination to the /users API endpoint"
NOTE = "<task-notification>\nWorker fix-verify finished: READY pr=#70\n</task-notification>"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", LEAD)


def _project(tmp_path: Path, auto: bool = True) -> Path:
    root = tmp_path / "proj"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    extra = ["--auto"] if auto else []
    code, _, err = run(root, "init", "--enforce", "--enforce-scope", "settings", *extra)
    assert code == 0, err
    return root


def _prompt(root: Path, prompt: str, session: str = LEAD) -> str:
    event = {"session_id": session, "cwd": str(root), "prompt": prompt, "permission_mode": "default"}
    code, out = handle("user-prompt-submit", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _lead_on(root: Path) -> tuple[int, str, str]:
    """What the skill does: the user invokes `/susanoo-lead`, then Claude runs `susanoo lead on`."""
    assert _prompt(root, "/susanoo-lead fix the README typo") == ""
    return run(root, "lead", "on")


def _edit(root: Path, session: str = LEAD, path: str = "app.py") -> dict[str, str]:
    event = {
        "session_id": session,
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(root / path)},
        "permission_mode": "default",
    }
    code, out = handle("pre-tool-use", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"] if out else {}


def test_a_lead_is_not_routed_and_other_sessions_are(tmp_path: Path):
    root = _project(tmp_path)
    code, out, err = _lead_on(root)
    assert code == 0, err
    assert "now a lead" in out
    note = _prompt(root, TASK)
    assert note.startswith("Susanoo: lead session") and "\n" not in note
    assert RecordStore(root).read("routes") == []
    # Another session in the same project is routed exactly as before.
    assert "Susanoo routed this task automatically" in _prompt(root, TASK, WORKER)
    routes = RecordStore(root).read("routes")
    assert [r["session_id"] for r in routes] == [WORKER]


def test_a_leads_message_is_still_recorded_as_unrouted(tmp_path: Path):
    """The floor and the user's requests are kept, and the route-before-edit gate still applies."""
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    _prompt(root, "Add Stripe billing with refunds to the users endpoint, work autonomously")
    record = prompt_state.load(root, LEAD)
    assert record is not None
    assert record["kind"] == AMBIGUOUS and record["hands_off"] is True
    assert record["floor"] and record["floor"]["profile"] == "deep"
    first = _edit(root)
    assert first.get("permissionDecision") == "deny" and "was not routed" in first["permissionDecisionReason"]
    assert _edit(root) == {}  # denied once per message, as for any unrouted message


def test_app_messages_to_a_lead_get_the_short_line_and_keep_the_floor(tmp_path: Path):
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    _prompt(root, "Add Stripe billing with refunds to the users endpoint")
    floor = prompt_state.load(root, LEAD)["floor"]  # type: ignore[index]
    note = _prompt(root, NOTE)
    assert note.startswith("Susanoo: lead session")
    record = prompt_state.load(root, LEAD)
    assert record is not None and record["from_app"] is True and record["floor"] == floor


@pytest.mark.parametrize("prompt", ["?add a column to the users table", "/susanoo add a column to users"])
def test_prompts_that_ask_for_no_routing_get_nothing(tmp_path: Path, prompt: str):
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    assert _prompt(root, prompt) == ""


def test_lead_off_routes_again(tmp_path: Path):
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    code, out, _ = run(root, "lead", "off")
    assert code == 0 and "no longer a lead" in out
    assert "Susanoo routed this task automatically" in _prompt(root, TASK)
    code, out, _ = run(root, "lead", "off")
    assert code == 0 and "was not a lead" in out


def test_lead_needs_a_session_and_takes_one_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _project(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    code, _, err = run(root, "lead", "on")
    assert code != 0 and "session id" in err
    # Without the session id variable (a terminal, or an agent clearing it), the named session
    # still needs the user's own `/susanoo-lead`.
    code, _, err = run(root, "lead", "on", "--session", WORKER)
    assert code != 0 and "/susanoo-lead" in err and not prompt_state.is_lead(root, WORKER)
    _prompt(root, "/susanoo-lead", WORKER)
    code, out, err = run(root, "lead", "on", "--session", WORKER, "--json")
    assert code == 0, err
    data = json.loads(out)
    assert data == {
        "session": WORKER,
        "lead": True,
        "was_lead": False,
        "automatic_routing": True,
        "open_route": None,
    }
    assert prompt_state.is_lead(root, WORKER) and not prompt_state.is_lead(root, LEAD)


def test_lead_on_names_an_open_route_and_keeps_its_gates(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, TASK)
    [route] = RecordStore(root).read("routes")
    code, out, _ = _lead_on(root)
    assert code == 0 and f"Route {route['id']} is still open" in out
    # The open route is neither continued, closed, nor replaced, and a lead's words grant it nothing.
    _prompt(root, "Add Stripe billing with refunds to the users endpoint, work autonomously")
    assert len(RecordStore(root).read("routes")) == 1
    assert RecordStore(root).read("outcomes") == []
    assert RecordStore(root).read("approvals") == []
    # Its gates still apply: after an edit, the turn cannot end without the required evidence.
    assert _edit(root).get("permissionDecision") != "deny"
    code, out = handle("stop", {"session_id": LEAD, "cwd": str(root), "permission_mode": "default"})
    assert code == 0 and json.loads(out)["decision"] == "block"


def test_only_the_user_turns_lead_mode_on(tmp_path: Path):
    """Lead mode stops routing, so it needs the user's `/susanoo-lead`, as a lighter workflow does."""
    root = _project(tmp_path)
    _prompt(root, "make this session the lead")
    code, _, err = run(root, "lead", "on")
    assert code != 0 and "/susanoo-lead" in err
    assert not prompt_state.is_lead(root, LEAD)
    # Another session cannot be marked from inside a Claude Code session, even after the request.
    _prompt(root, "/susanoo-lead")
    code, _, err = run(root, "lead", "on", "--session", WORKER)
    assert code != 0 and "only that session" in err
    assert not prompt_state.is_lead(root, WORKER)
    assert _lead_on(root)[0] == 0
    # Once a lead, `lead on` again is harmless, and `lead off` is always allowed.
    _prompt(root, TASK)
    assert run(root, "lead", "on")[0] == 0
    assert run(root, "lead", "off")[0] == 0


def test_a_leads_questions_get_the_lead_line_and_its_skill_prompt_nothing(tmp_path: Path):
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    record = prompt_state.load(root, LEAD)
    assert record is not None and record["lead_requested"] is True and record["skill"] is False
    assert _prompt(root, "What does app.py do?").startswith("Susanoo: lead session")
    assert prompt_state.load(root, LEAD)["lead_requested"] is False  # type: ignore[index]


def test_the_ledger_is_not_stopped_by_the_route_before_edit_gate(tmp_path: Path):
    root = _project(tmp_path)
    assert _lead_on(root)[0] == 0
    _prompt(root, TASK)
    assert _edit(root, path=".git/susanoo/lead.md") == {}
    # Susanoo's records beside it are not exempt, and neither is a project file.
    assert _edit(root, path=".git/susanoo/records/reviews.jsonl").get("permissionDecision") == "deny"


def test_a_lead_request_after_an_app_block_counts(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "<system-reminder>\nLinked sessions now: none\n</system-reminder>\n/susanoo-lead")
    assert run(root, "lead", "on")[0] == 0


def test_lead_on_marks_the_project_the_hook_sees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Without --path, the project is `CLAUDE_PROJECT_DIR`, as for the hook, not the shell's folder."""
    import io

    from susanoo.cli import main

    root = _project(tmp_path)
    _prompt(root, "/susanoo-lead")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    out, err = io.StringIO(), io.StringIO()
    assert main(["lead", "on"], stdin=io.StringIO(""), stdout=out, stderr=err) == 0, err.getvalue()
    assert prompt_state.is_lead(root, LEAD)
    assert _prompt(root, TASK).startswith("Susanoo: lead session")


def test_lead_on_without_automatic_routing_says_so(tmp_path: Path):
    root = _project(tmp_path, auto=False)
    code, out, _ = _lead_on(root)
    assert code == 0 and "Automatic routing is off" in out and "ask them in chat before each merge" in out
    # Nothing is marked, so turning automatic routing on later leaves the session routed.
    assert not prompt_state.is_lead(root, LEAD)
    assert _prompt(root, TASK) == ""  # nothing is routed or noted without automatic routing


def test_lead_needs_local_records(tmp_path: Path):
    root = tmp_path / "bare"
    write(root, "app.py", "x = 1\n")
    code, _, err = run(root, "lead", "on")
    assert code != 0 and "susanoo init" in err


@pytest.mark.parametrize("content", ["not json", "[]", '{"lead": "yes"}', '{"lead": 1}'])
def test_a_damaged_mark_leaves_the_session_routed(tmp_path: Path, content: str):
    root = _project(tmp_path)
    path = prompt_state.session_file(root, LEAD, ".lead")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert not prompt_state.is_lead(root, LEAD)
    assert "Susanoo routed this task automatically" in _prompt(root, TASK)
