from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from susanoo import __version__
from susanoo.cli import main


def run(root: Path, *args: str, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([*args, "--path", str(root)], stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDECODE", raising=False)


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"susanoo {__version__}" in capsys.readouterr().out
    assert main([], stdout=io.StringIO()) == 2


def test_assess_text_and_json(python_project: Path):
    code, out, _ = run(python_project, "assess", "Fix", "typo", "in", "README")
    assert code == 0 and "Complexity:  LOW" in out
    code, out, _ = run(python_project, "assess", "--json", "Add Stripe checkout")
    data = json.loads(out)
    assert data["risk"]["level"] in ("high", "critical")
    assert data["dimensions"]["security_privacy"]["label"] == "high"


def test_assess_from_stdin(python_project: Path):
    code, out, _ = run(python_project, "assess", "--stdin", "--json", stdin="Fix typo\nin README\n")
    assert code == 0 and json.loads(out)["task"] == "Fix typo in README"


def test_missing_task_is_an_error(python_project: Path):
    code, _, err = run(python_project, "assess")
    assert code == 1 and "no task description" in err


def test_route_brief_contains_all_sections(python_project: Path):
    code, out, err = run(python_project, "route", "Build a SaaS dashboard with authentication and billing")
    assert code == 0
    for heading in (
        "## Task",
        "## Assessment",
        "## Recommended mode: DEEP",
        "## Mandatory safety controls",
        "## Capabilities",
        "## Proposed steps",
        "## Required verification",
        "## Checkpoints",  # "## Approval checkpoints" with hands_off = false
        "## Limitations",
    ):
        assert heading in out
    assert "not recorded" in err


def test_route_json_and_override(python_project: Path):
    _, out, _ = run(
        python_project, "route", "--json", "--profile", "fast", "Change password hashing to bcrypt"
    )
    data = json.loads(out)
    assert data["route"]["profile"] == "fast"
    assert data["route"]["overridden"] is True
    assert "security-review" in [c["id"] for c in data["route"]["controls"]]
    assert {s["id"] for s in data["verification"]["steps"]} >= {"security_scan", "independent_review"}


def test_full_record_lifecycle(python_project: Path):
    assert run(python_project, "init")[0] == 0
    assert (python_project / ".claude/skills/susanoo/SKILL.md").is_file()
    code, out, _ = run(python_project, "route", "--json", "Fix the crash when saving settings")
    record_id = json.loads(out)["record_id"]
    assert record_id and record_id.startswith("su-")

    code, out, _ = run(python_project, "verify")
    assert code == 0 and "Nothing was run" in out

    code, out, _ = run(
        python_project,
        "evidence",
        "latest",
        "--step",
        "unit_tests",
        "--status",
        "passed",
        "--command",
        "pytest",
    )
    assert code == 0 and "source: reported" in out
    code, out, err = run(
        python_project,
        "outcome",
        record_id[:15],
        "--status",
        "completed",
        "--minutes",
        "12",
        "--source",
        "agent",
        "--agents",
        "susanoo-reviewer",
        "--tokens",
        "1000",
    )
    # unit_tests is runnable, so a reported result is not enough for an agent to claim completion.
    assert code == 1 and "lacks passing evidence" in err
    code, out, err = run(
        python_project,
        "outcome",
        record_id[:15],
        "--status",
        "completed",
        "--minutes",
        "12",
        "--source",
        "agent",
        "--tokens",
        "1000",
        "--accept-missing-evidence",
    )
    assert code == 0

    code, out, _ = run(python_project, "report", "--json")
    data = json.loads(out)
    [fast] = [p for p in data["profiles"] if p["profile"] == "fast"]
    assert fast["completed"] == 1 and fast["reported_checks"] == 1
    code, out, _ = run(python_project, "report")
    assert record_id in out and "completed" in out


def test_outcome_requires_init(python_project: Path):
    code, _, err = run(python_project, "outcome", "latest", "--status", "completed")
    assert code == 1 and "susanoo init" in err


def test_outcome_rejects_negative_numbers(python_project: Path):
    run(python_project, "init")
    run(python_project, "route", "Fix typo in README")
    code, _, err = run(python_project, "outcome", "latest", "--status", "completed", "--tokens", "-5")
    assert code == 1 and "negative" in err


def test_store_task_text_false_hashes_only(python_project: Path):
    run(python_project, "init")
    (python_project / ".susanoo/config.toml").write_text(
        "schema_version = 1\n[records]\nstore_task_text = false\n", encoding="utf-8"
    )
    run(python_project, "route", "Secret project codename Falcon")
    text = (python_project / ".susanoo/records/routes.jsonl").read_text(encoding="utf-8")
    assert "Falcon" not in text and "task_sha256" in text


def test_init_dry_run_and_uninstall(python_project: Path):
    code, out, _ = run(python_project, "init", "--dry-run")
    assert "dry run" in out and not (python_project / ".susanoo").exists()
    run(python_project, "init")
    code, out, _ = run(python_project, "init", "--uninstall")
    assert code == 0 and "remove" in out
    assert not (python_project / ".claude/skills/susanoo").exists()


def test_doctor_json(python_project: Path):
    code, out, _ = run(python_project, "doctor", "--json")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True
    assert {c["check"] for c in data["checks"]} >= {"python", "configuration", "/susanoo skill"}


def test_doctor_reports_invalid_config(python_project: Path):
    (python_project / ".susanoo").mkdir()
    (python_project / ".susanoo/config.toml").write_text("schema_version = 99\n", encoding="utf-8")
    code, out, _ = run(python_project, "doctor")
    assert code == 1 and "[fail]" in out


def test_inventory_json(python_project: Path):
    _, out, _ = run(python_project, "inventory", "--json")
    data = json.loads(out)
    assert data["languages"]["python"] == 3
    assert any(f["id"] == "tool:pytest" for f in data["findings"])


def test_research_and_list(python_project: Path):
    code, out, _ = run(python_project, "research", "--list")
    assert "browser_testing" in out
    code, out, _ = run(python_project, "research", "--json", "payment_sandbox")
    assert json.loads(out)["decision"] in ("adopt", "use-existing")
    code, _, err = run(python_project, "research", "nonsense")
    assert code == 1 and "unknown capability" in err


def test_eval_threshold(python_project: Path):
    assert run(python_project, "eval", "--min-agreement", "0.5")[0] == 0
    code, _, err = run(python_project, "eval", "--min-agreement", "1.01")
    assert code == 1 and "below" in err


def test_invalid_path():
    out, err = io.StringIO(), io.StringIO()
    assert main(["doctor", "--path", "/definitely/not/here"], stdout=out, stderr=err) == 2
