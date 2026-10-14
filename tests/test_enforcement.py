"""Enforcement gates and the Claude Code hook bridge, tested without Claude Code."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from susanoo.adapters.claude_code.installer import (
    apply_install,
    enforcement_enabled,
    plan_install,
    skill_template,
)
from susanoo.cli import main
from susanoo.enforcement import gates
from susanoo.enforcement.state import RouteState, evidence_status
from susanoo.evaluation import RecordStore
from tests.conftest import GATED_TOML, gated_config

SESSION = "session-123"


def route(profile: str = "standard", controls: list[str] | None = None, **extra) -> dict:
    data = {
        "id": "su-1",
        "profile": profile,
        "session_id": SESSION,
        "controls": controls or [],
        "risk": {"level": "moderate"},
        "complexity": {"level": "moderate"},
        "phases": ["Investigate", "Plan", "Implement"],
        "verification": [
            {"id": "unit_tests", "level": "required", "mode": "ready"},
            {"id": "independent_review", "level": "required", "mode": "manual"},
            {"id": "type_check", "level": "recommended", "mode": "ready"},
        ],
    }
    data.update(extra)
    return data


def state(profile: str = "standard", approved: bool = False, controls=None, missing=None, failing=None):
    return RouteState(
        route=route(profile, controls),
        approved=approved,
        closed=False,
        missing=list(missing or []),
        failing=list(failing or []),
    )


@pytest.fixture(scope="module")
def cfg():
    # These tests exercise the gates themselves; by default (hands-off) Susanoo asks nothing.
    return gated_config()


def edit_event(path: str) -> dict:
    return {"session_id": SESSION, "tool_name": "Edit", "tool_input": {"file_path": path}}


def bash_event(command: str) -> dict:
    return {"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": command}}


# --- evidence status ----------------------------------------------------------------


def test_evidence_status_rules():
    r = route()
    assert evidence_status(r, []) == (["unit_tests", "independent_review"], [])
    ev = [
        {"record_id": "su-1", "step": "unit_tests", "source": "reported", "status": "passed"},
        {"record_id": "su-1", "step": "independent_review", "source": "reported", "status": "passed"},
    ]
    # A reported result does not satisfy a runnable (ready) step.
    assert evidence_status(r, ev) == (["unit_tests"], [])
    ev.append({"record_id": "su-1", "step": "unit_tests", "source": "executed", "status": "failed"})
    assert evidence_status(r, ev) == ([], ["unit_tests"])
    ev.append({"record_id": "su-1", "step": "unit_tests", "source": "executed", "status": "passed"})
    assert evidence_status(r, ev) == ([], [])
    # Evidence for other records never counts.
    assert evidence_status(route(), [{**e, "record_id": "su-2"} for e in ev])[0]


# --- plan-approval gate -------------------------------------------------------------


def test_edits_denied_until_plan_approved(tmp_path: Path, cfg):
    target = str(tmp_path / "src" / "app.py")
    d = gates.pre_tool_use(edit_event(target), state("standard"), tmp_path, cfg)
    assert d.permission == "deny" and "approve" in d.reason
    assert (
        gates.pre_tool_use(edit_event(target), state("standard", approved=True), tmp_path, cfg).permission
        is None
    )


def test_fast_profile_and_no_route_are_not_gated(tmp_path: Path, cfg):
    target = str(tmp_path / "a.py")
    assert gates.pre_tool_use(edit_event(target), state("fast"), tmp_path, cfg).permission is None
    assert gates.pre_tool_use(edit_event(target), None, tmp_path, cfg).permission is None


def test_edits_outside_the_project_are_not_gated(tmp_path: Path, cfg):
    root = tmp_path / "proj"
    root.mkdir()
    outside = str(tmp_path / "scratch" / "notes.md")
    assert gates.pre_tool_use(edit_event(outside), state("deep"), root, cfg).permission is None
    assert gates.pre_tool_use(edit_event("relative/file.py"), state("deep"), root, cfg).permission == "deny"


def test_approval_command_always_requires_user_prompt(tmp_path: Path, cfg):
    for command in ("susanoo approve", "python -m susanoo approve latest", "cd x && susanoo approve su-1"):
        assert gates.pre_tool_use(bash_event(command), None, tmp_path, cfg).permission == "ask"
    assert (
        gates.pre_tool_use(
            bash_event("susanoo outcome latest --accept-missing-evidence"), None, tmp_path, cfg
        ).permission
        == "ask"
    )


@pytest.mark.parametrize(
    "command, asks",
    [
        ("pytest -q", False),
        ("git status && git diff", False),
        ("grep -rn foo src 2>/dev/null", False),
        ("python -m susanoo report --json", False),
        ('grep -n "a->b" src', False),
        ('rg "=>" src', False),
        ('git log --format="%h -> %s"', False),
        ('python -c "print(1 > 0)"', False),
        ("make build 2> err.txt", True),
        ("sed -E -i 's/a/b/' f.py", True),
        ("echo hi > notes.txt", True),
        ("sed -i 's/a/b/' file.py", True),
        ("rm build.log", True),
        ("git commit -m wip", True),
        ("npm install left-pad", True),
        ("Set-Content -Path a.txt -Value x", True),
    ],
)
def test_shell_writes_before_approval(tmp_path: Path, cfg, command: str, asks: bool):
    d = gates.pre_tool_use(bash_event(command), state("standard"), tmp_path, cfg)
    assert (d.permission == "ask") is asks
    assert (
        gates.pre_tool_use(bash_event(command), state("standard", approved=True), tmp_path, cfg).permission
        is None
    )


# --- irreversible-action gate -------------------------------------------------------


@pytest.mark.parametrize(
    "command, asks",
    [
        ("git push origin main", True),
        ("git push --force", True),
        ("git reset --hard HEAD~3", True),
        ("terraform apply -auto-approve", True),
        ("kubectl delete pod x", True),
        ("npm publish", True),
        ("alembic upgrade head", True),
        ("psql -c 'DROP TABLE orders'", True),
        ("rm -rf dist", True),
        ("git log --oneline", False),
        ("terraform plan", False),
        ("kubectl get pods", False),
        ("alembic history", False),
    ],
)
def test_irreversible_gate(tmp_path: Path, cfg, command: str, asks: bool):
    active = state("deep", approved=True, controls=["irreversible-action-gate"])
    assert (gates.pre_tool_use(bash_event(command), active, tmp_path, cfg).permission == "ask") is asks
    inactive = state("deep", approved=True)
    if command.startswith("git push"):
        assert gates.pre_tool_use(bash_event(command), inactive, tmp_path, cfg).permission is None


# --- evidence gate ------------------------------------------------------------------


def test_stop_blocked_until_evidence(cfg):
    blocked = gates.stop({"stop_hook_active": False}, state(approved=True, missing=["unit_tests"]), cfg, 0)
    assert (
        blocked.block_stop and "unit_tests" in blocked.reason and "susanoo verify --execute" in blocked.reason
    )
    assert not gates.stop({}, state(approved=True), cfg, 0).block_stop
    assert not gates.stop({}, None, cfg, 0).block_stop


def test_stop_gate_waits_for_approval_and_work(cfg):
    # Ending a turn to present a plan or ask a question must not be blocked.
    assert not gates.stop({}, state(missing=["unit_tests"]), cfg, 0).block_stop
    assert not gates.stop({}, state("fast", missing=["unit_tests"]), cfg, 0, work_started=False).block_stop
    assert gates.stop({}, state("fast", missing=["unit_tests"]), cfg, 0, work_started=True).block_stop


def test_stop_gate_cannot_loop_forever(cfg):
    s = state(approved=True, missing=["unit_tests"])
    assert gates.stop({"stop_hook_active": True}, s, cfg, 2).block_stop
    released = gates.stop({"stop_hook_active": True}, s, cfg, 3)
    assert not released.block_stop and released.notes


def test_summary_mentions_state():
    text = gates.summary(state("deep", controls=["security-review"], missing=["security_scan"]))
    assert "DEEP" in text and "NO" in text and "security-review" in text and "security_scan" in text


# --- hook bridge through the CLI ----------------------------------------------------


def hook(root: Path, event: str, payload: dict, monkeypatch) -> tuple[int, str]:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    out = io.StringIO()
    code = main(["hook", event], stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    out = io.StringIO()
    assert main(["init", "--enforce", "--path", str(root)], stdout=out, stderr=io.StringIO()) == 0
    with (root / ".susanoo" / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write(GATED_TOML)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    assert (
        main(
            ["route", "--path", str(root), "--profile", "standard", "Add a settings page"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )
    return root


def test_route_records_session_id(project: Path):
    [record] = RecordStore(project).read("routes")
    assert record["session_id"] == SESSION and record["profile"] == "standard"


def test_hook_end_to_end(project: Path, monkeypatch):
    target = str(project / "app.py")
    code, out = hook(project, "pre-tool-use", edit_event(target), monkeypatch)
    decision = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and decision["permissionDecision"] == "deny"

    # Other sessions are never affected.
    code, out = hook(project, "pre-tool-use", {**edit_event(target), "session_id": "other"}, monkeypatch)
    assert out == ""

    assert main(["approve", "--path", str(project)], stdout=io.StringIO()) == 0
    code, out = hook(project, "pre-tool-use", edit_event(target), monkeypatch)
    assert out == ""

    code, out = hook(project, "stop", {"session_id": SESSION, "stop_hook_active": False}, monkeypatch)
    assert json.loads(out)["decision"] == "block"

    # Recording an honest outcome closes the route and releases every gate.
    assert (
        main(
            ["outcome", "--path", str(project), "latest", "--status", "partial", "--source", "agent"],
            stdout=io.StringIO(),
        )
        == 0
    )
    code, out = hook(project, "stop", {"session_id": SESSION}, monkeypatch)
    assert out == ""
    assert RecordStore(project).read("enforcement")


def test_stop_guard_counts_and_releases(project: Path, monkeypatch):
    assert main(["approve", "--path", str(project)], stdout=io.StringIO()) == 0
    hook(project, "pre-tool-use", edit_event(str(project / "app.py")), monkeypatch)  # work starts
    payload = {"session_id": SESSION, "stop_hook_active": True}
    results = [
        json.loads(o)["decision"] if o else None
        for _, o in (hook(project, "stop", payload, monkeypatch) for _ in range(5))
    ]
    assert results[:3] == ["block", "block", "block"]
    assert results[3] is None  # released after max_stop_blocks consecutive blocks


def test_compaction_reinjects_summary(project: Path, monkeypatch):
    code, out = hook(project, "session-start", {"session_id": SESSION, "source": "compact"}, monkeypatch)
    assert "Susanoo: active route" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    hook(project, "post-compact", {"session_id": SESSION, "trigger": "auto"}, monkeypatch)
    code, out = hook(project, "pre-tool-use", bash_event("git status"), monkeypatch)
    assert "additionalContext" in json.loads(out)["hookSpecificOutput"]
    _code, out = hook(project, "pre-tool-use", bash_event("git status"), monkeypatch)
    assert out == ""  # injected once


def test_hooks_are_inert_in_uninitialized_projects(tmp_path: Path, monkeypatch):
    code, out = hook(tmp_path, "pre-tool-use", edit_event(str(tmp_path / "x.py")), monkeypatch)
    assert (code, out) == (0, "")


def test_bad_hook_input_is_a_non_blocking_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    err = io.StringIO()
    code = main(["hook", "stop"], stdin=io.StringIO("{not json"), stdout=io.StringIO(), stderr=err)
    assert code == 1  # exit 1 is shown as a hook error; only exit 2 would block


# --- installation -------------------------------------------------------------------


def test_enforce_choice_persists_across_init(tmp_path: Path):
    apply_install(tmp_path, plan_install(tmp_path, enforce=True), enforce=True)
    assert enforcement_enabled(tmp_path)
    skill = tmp_path / ".claude/skills/susanoo/SKILL.md"
    assert skill.read_text(encoding="utf-8") == skill_template(True)
    out = io.StringIO()
    main(["init", "--path", str(tmp_path)], stdout=out, stderr=io.StringIO())
    assert skill.read_text(encoding="utf-8") == skill_template(True)
    main(["init", "--no-enforce", "--path", str(tmp_path)], stdout=io.StringIO(), stderr=io.StringIO())
    assert skill.read_text(encoding="utf-8") == skill_template(False)
    assert not enforcement_enabled(tmp_path)


def test_reported_failures_are_not_passing_evidence():
    r = route()
    ev = [
        {"record_id": "su-1", "step": "unit_tests", "source": "executed", "status": "passed"},
        {"record_id": "su-1", "step": "independent_review", "source": "reported", "status": "failed"},
    ]
    assert evidence_status(r, ev) == ([], ["independent_review"])
    ev.append({"record_id": "su-1", "step": "independent_review", "source": "reported", "status": "skipped"})
    assert evidence_status(r, ev) == ([], [])


@pytest.mark.parametrize(
    "command",
    [
        "SUSANOO approve",
        "susanoo.exe approve latest",
        '"susanoo" approve',
        "python -m susanoo.cli approve",
    ],
)
def test_approval_variants_still_ask(tmp_path: Path, cfg, command: str):
    assert gates.pre_tool_use(bash_event(command), None, tmp_path, cfg).permission == "ask"


def test_closing_or_replacing_unapproved_route_asks(tmp_path: Path, cfg):
    unapproved = state("standard")
    for command in ("susanoo outcome latest --status partial --source agent", "susanoo route 'fix typo'"):
        assert gates.pre_tool_use(bash_event(command), unapproved, tmp_path, cfg).permission == "ask"
        assert (
            gates.pre_tool_use(
                bash_event(command), state("standard", approved=True), tmp_path, cfg
            ).permission
            is None
        )


def test_home_relative_paths_are_outside_project(tmp_path: Path, cfg):
    root = tmp_path / "proj"
    root.mkdir()
    assert gates.pre_tool_use(edit_event("~/notes.md"), state("deep"), root, cfg).permission is None


def test_evidence_without_command_flag(project: Path):
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "evidence",
            "--path",
            str(project),
            "latest",
            "--step",
            "independent_review",
            "--status",
            "skipped",
            "--note",
            "no reviewer available",
        ],
        stdout=out,
        stderr=err,
    )
    assert code == 0, err.getvalue()


def test_latest_is_scoped_to_the_session(project: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    main(["route", "--path", str(project), "Fix typo in README"], stdout=io.StringIO(), stderr=io.StringIO())
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    assert main(["approve", "--path", str(project)], stdout=io.StringIO()) == 0
    [approval] = RecordStore(project).read("approvals")
    [own] = [r for r in RecordStore(project).read("routes") if r["session_id"] == SESSION]
    assert approval["record_id"] == own["id"]


def test_stop_guard_is_per_session(project: Path, monkeypatch):
    assert main(["approve", "--path", str(project)], stdout=io.StringIO()) == 0
    hook(project, "pre-tool-use", edit_event(str(project / "app.py")), monkeypatch)
    payload = {"session_id": SESSION, "stop_hook_active": True}
    hook(project, "stop", payload, monkeypatch)
    hook(project, "stop", {"session_id": "someone-else", "stop_hook_active": True}, monkeypatch)
    hooks_dir = project / ".susanoo" / "records" / "hooks"
    [own] = [json.loads(p.read_text(encoding="utf-8")) for p in hooks_dir.glob("session-123*.json")]
    assert own["stop_blocks"] == 1


def test_default_config_blocks_stop_after_an_edit(tmp_path: Path, monkeypatch):
    """Hands-off (the default) lets the edit through without approval, then holds the turn for evidence."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert main(["init", "--enforce", "--path", str(root)], stdout=io.StringIO(), stderr=io.StringIO()) == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    route_args = ["route", "--path", str(root), "--profile", "standard", "Add a settings page"]
    assert main(route_args, stdout=io.StringIO(), stderr=io.StringIO()) == 0
    code, out = hook(root, "pre-tool-use", edit_event(str(root / "app.py")), monkeypatch)
    assert code == 0 and out == ""  # no approval needed
    code, out = hook(root, "stop", {"session_id": SESSION, "stop_hook_active": False}, monkeypatch)
    assert json.loads(out)["decision"] == "block"
