from __future__ import annotations

import sys
from pathlib import Path

import pytest

from susanoo.analyzer import CapabilityRequirement, assess
from susanoo.discovery import Finding, Inventory, Status
from susanoo.router import route
from susanoo.selection import candidates_for, recommend
from susanoo.verification import (
    VerificationPlan,
    VerificationStep,
    build_plan,
    execute_plan,
    redact,
    reported_evidence,
    select_steps,
    verification_requirements,
)


def plan_for(task, config, profiles, registry, findings=(), override=None):
    a = assess(task, config)
    d = route(a, profiles, config, override=override)
    selected = select_steps(a, d.profile, d.controls, config)
    inv = Inventory(".", "", False, {"python": 3}, list(findings))
    recs = recommend(verification_requirements(selected), inv, registry)
    return build_plan(d.profile, selected, recs)


def levels(plan: VerificationPlan) -> dict[str, str]:
    return {s.id: s.level for s in plan.steps}


def test_depth_scales_with_profile(config, profiles, registry):
    fast = levels(plan_for("Fix typo in README", config, profiles, registry))
    deep = levels(plan_for("Refactor the monolith into microservices", config, profiles, registry))
    assert fast["unit_tests"] == "required"
    assert "independent_review" not in fast and "security_scan" not in fast
    assert deep["integration_tests"] == "required"
    assert deep["independent_review"] == "required"
    assert len(deep) > len(fast)


def test_risk_escalates_verification_even_when_overridden(config, profiles, registry):
    plan = plan_for(
        "Change password hashing from SHA1 to bcrypt", config, profiles, registry, override="fast"
    )
    lv = levels(plan)
    assert lv["security_scan"] == "required"
    assert lv["independent_review"] == "required"
    assert any("mandatory control" in r for r in plan.get("security_scan").reasons)


def test_data_risk_requires_migration_check(config, profiles, registry):
    assert (
        levels(plan_for("Add a column to the users table with a migration", config, profiles, registry))[
            "migration_check"
        ]
        == "required"
    )


def test_step_modes_reflect_tools(config, profiles, registry):
    pytest_finding = Finding(
        "tool:pytest",
        "tool",
        "pytest",
        Status.INSTALLED_UNVERIFIED,
        ["unit_testing", "integration_testing"],
        tool_kind="cli",
    )
    plan = plan_for("Fix the crash when saving settings", config, profiles, registry, [pytest_finding])
    unit = plan.get("unit_tests")
    assert unit.mode == "ready" and unit.command == ["pytest", "-q"]
    assert plan.get("static_analysis").mode == "unavailable"


def _environment(status: Status) -> Finding:
    return Finding(
        "environment:poetry",
        "environment",
        "Poetry",
        status,
        detail={"binary": "poetry", "run_prefix": ["poetry", "run"]},
    )


_PYTEST = Finding(
    "tool:pytest", "tool", "pytest", Status.INSTALLED_UNVERIFIED, ["unit_testing"], tool_kind="cli"
)


def test_python_checks_run_in_external_environment(config, profiles, registry):
    findings = [_PYTEST, _environment(Status.INSTALLED_UNVERIFIED)]
    unit = plan_for("Fix the crash when saving settings", config, profiles, registry, findings).get(
        "unit_tests"
    )
    assert unit.mode == "ready" and unit.command == ["poetry", "run", "pytest", "-q"]


def test_python_checks_become_manual_without_environment_manager(config, profiles, registry):
    findings = [_PYTEST, _environment(Status.CONFIGURED_INACCESSIBLE)]
    unit = plan_for("Fix the crash when saving settings", config, profiles, registry, findings).get(
        "unit_tests"
    )
    # pytest from PATH would run without the project's dependencies; never record that as evidence.
    assert unit.mode == "manual" and unit.command is None
    assert "'poetry' is not on PATH" in unit.withheld


def test_tools_declared_in_external_environment_are_usable(registry):
    # pytest is only installed in the Poetry environment: declared, but no binary on PATH.
    declared = Finding(
        "tool:pytest",
        "tool",
        "pytest",
        Status.CONFIGURED_INACCESSIBLE,
        ["unit_testing"],
        tool_kind="cli",
        detail={"registry_id": "pytest", "python_declared": True},
    )
    inv = Inventory(".", "", False, {"python": 3}, [declared, _environment(Status.INSTALLED_UNVERIFIED)])
    [rec] = recommend([CapabilityRequirement("unit_testing", "required")], inv, registry)
    assert rec.selected is not None and rec.selected.id == "pytest"
    assert rec.selected.status is Status.INSTALLED_UNVERIFIED
    assert rec.selected.command == ["poetry", "run", "pytest", "-q"]

    without_manager = Inventory(
        ".", "", False, {"python": 3}, [declared, _environment(Status.CONFIGURED_INACCESSIBLE)]
    )
    [rec] = recommend([CapabilityRequirement("unit_testing", "required")], without_manager, registry)
    assert rec.selected is None


def test_python_scripts_also_run_in_external_environment(registry):
    django = Finding(
        "tool:django-migrations",
        "tool",
        "Django migrations",
        Status.INSTALLED_UNVERIFIED,
        ["migration_checks"],
        tool_kind="script",
    )
    inv = Inventory(".", "", False, {"python": 3}, [django, _environment(Status.INSTALLED_UNVERIFIED)])
    candidate = next(
        c for c in candidates_for("migration_checks", inv, registry) if c.id == "django-migrations"
    )
    assert candidate.command is not None and candidate.command[:3] == ["poetry", "run", "python"]


def test_withheld_tool_ranks_after_runnable_alternative(registry):
    make_test = Finding(
        "tool:make-test",
        "tool",
        "make test",
        Status.INSTALLED_UNVERIFIED,
        ["unit_testing"],
        tool_kind="script",
    )
    inv = Inventory(
        ".", "", False, {"python": 3}, [_PYTEST, make_test, _environment(Status.CONFIGURED_INACCESSIBLE)]
    )
    [rec] = recommend([CapabilityRequirement("unit_testing", "required")], inv, registry)
    assert rec.selected is not None and rec.selected.id == "make-test"
    assert rec.selected.command_withheld == ""


def test_withheld_reason_is_shown_in_brief(tmp_path: Path, config):
    from susanoo.pipeline import plan_task
    from susanoo.render import render_brief

    inv = Inventory(
        str(tmp_path), "", False, {"python": 3}, [_PYTEST, _environment(Status.CONFIGURED_INACCESSIBLE)]
    )
    plan = plan_task("Fix the crash when saving settings", tmp_path, config, inventory=inv)
    unit = plan.verification.get("unit_tests")
    assert unit.mode == "manual" and unit.withheld
    assert f"pytest (performed by the agent or a person) - {unit.withheld}" in render_brief(plan)


def test_withheld_reason_reaches_auto_brief_and_skipped_evidence(tmp_path: Path, config):
    from susanoo.enforcement.auto import compact_brief
    from susanoo.pipeline import plan_task

    inv = Inventory(
        str(tmp_path), "", False, {"python": 3}, [_PYTEST, _environment(Status.CONFIGURED_INACCESSIBLE)]
    )
    plan = plan_task("Fix the crash when saving settings", tmp_path, config, inventory=inv)
    unit = plan.verification.get("unit_tests")
    assert f"Unit tests ({unit.withheld})" in compact_brief(plan, "r1", False, "susanoo")
    [evidence] = [e for e in execute_plan(plan.verification, tmp_path) if e.step == "unit_tests"]
    assert evidence.status == "skipped" and unit.withheld in evidence.note


def test_withheld_reason_is_kept_when_tool_is_a_gap(tmp_path: Path, config):
    from susanoo.pipeline import plan_task
    from susanoo.render import render_brief

    # pytest declared but on no PATH, and the manager is missing too: still a gap, with the reason.
    declared = Finding(
        "tool:pytest",
        "tool",
        "pytest",
        Status.CONFIGURED_INACCESSIBLE,
        ["unit_testing"],
        tool_kind="cli",
        detail={"registry_id": "pytest", "python_declared": True},
    )
    inv = Inventory(
        str(tmp_path), "", False, {"python": 3}, [declared, _environment(Status.CONFIGURED_INACCESSIBLE)]
    )
    plan = plan_task("Fix the crash when saving settings", tmp_path, config, inventory=inv)
    unit = plan.verification.get("unit_tests")
    assert unit.mode == "unavailable" and "'poetry' is not on PATH" in unit.withheld
    assert f"no usable tool - gap - {unit.withheld}" in render_brief(plan)


def test_non_python_tools_ignore_python_environment(registry):
    jest = Finding(
        "tool:jest", "tool", "Jest", Status.INSTALLED_UNVERIFIED, ["unit_testing"], tool_kind="cli"
    )
    inv = Inventory(".", "", False, {"typescript": 3}, [jest, _environment(Status.CONFIGURED_INACCESSIBLE)])
    candidate = next(c for c in candidates_for("unit_testing", inv, registry) if c.id == "jest")
    assert candidate.command == ["npx", "--no-install", "jest"] and candidate.command_withheld == ""


def _step(
    step_id: str, command: list[str] | None, mode: str = "ready", level: str = "required"
) -> VerificationStep:
    return VerificationStep(step_id, step_id, "unit_testing", level, [], tool="t", command=command, mode=mode)


def test_executor_records_real_exit_codes(tmp_path: Path):
    ok = [sys.executable, "-c", "print('fine')"]
    bad = [sys.executable, "-c", "import sys; print('token=abc123'); sys.exit(3)"]
    plan = VerificationPlan(
        "standard",
        [
            _step("ok", ok),
            _step("bad", bad),
            _step("dup", ok),
            _step("review", None, mode="manual"),
            _step("gap", None, mode="unavailable", level="recommended"),
        ],
    )
    results = {e.step: e for e in execute_plan(plan, tmp_path, timeout=60)}
    assert results["ok"].status == "passed" and results["ok"].exit_code == 0
    assert results["bad"].status == "failed" and results["bad"].exit_code == 3
    assert "abc123" not in results["bad"].output_tail and "[REDACTED]" in results["bad"].output_tail
    assert results["dup"].note.startswith("same command")
    assert results["review"].status == "skipped" and "agent or a person" in results["review"].note
    assert results["gap"].status == "skipped"
    assert all(e.source == "executed" for e in results.values())


def test_executor_filters(tmp_path: Path):
    ok = [sys.executable, "-c", "pass"]
    plan = VerificationPlan("deep", [_step("a", ok), _step("b", ok, level="recommended")])
    assert [e.step for e in execute_plan(plan, tmp_path, only={"b"})] == ["b"]
    assert [e.step for e in execute_plan(plan, tmp_path, required_only=True)] == ["a"]


def test_missing_executable_and_timeout(tmp_path: Path):
    plan = VerificationPlan(
        "fast",
        [
            _step("missing", ["definitely-not-installed-xyz"]),
            _step("slow", [sys.executable, "-c", "import time; time.sleep(5)"]),
        ],
    )
    results = {e.step: e for e in execute_plan(plan, tmp_path, timeout=0.5)}
    assert results["missing"].status == "error"
    assert results["slow"].status == "timeout"


def test_reported_evidence_is_marked_reported():
    e = reported_evidence("browser_tests", "passed", "playwright test", note="password: hunter2")
    assert e.source == "reported" and e.exit_code is None
    assert "hunter2" not in e.note
    with pytest.raises(ValueError):
        reported_evidence("x", "probably-fine")


def test_redact_common_formats():
    text = "sk_live_ABCDEFGHIJKL ghp_" + "a" * 30 + " AKIA" + "B" * 16 + " api_key=xyz"
    out = redact(text)
    assert "ABCDEFGHIJKL" not in out and "aaaa" not in out and "xyz" not in out and "AKIA" not in out


def test_dependency_change_recommends_an_audit(config):
    from susanoo.analyzer import assess
    from susanoo.verification import select_steps

    steps = {s.rule["id"]: s for s in select_steps(assess("Add the zod package", config), "fast", [], config)}
    assert steps["dependency_audit"].level == "recommended"
    assert any("dependency-change" in r for r in steps["dependency_audit"].reasons)
    plain = {s.rule["id"] for s in select_steps(assess("Add a settings page", config), "fast", [], config)}
    assert "dependency_audit" not in plain
