from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from susanoo.analyzer import CapabilityRequirement, assess
from susanoo.discovery import Finding, Inventory, Status, executables
from susanoo.pipeline import plan_task
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
from tests.conftest import write


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
    assert unit.mode == "ready" and unit.command == ["python", "-m", "pytest", "-q"]
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
    assert unit.mode == "ready" and unit.command == ["poetry", "run", "python", "-m", "pytest", "-q"]


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
    assert rec.selected.command == ["poetry", "run", "python", "-m", "pytest", "-q"]

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


def test_unit_tests_import_an_uninstalled_flat_layout_package(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, config
):
    # The A/B pilot's project: package at the root, not installed, conftest.py only in tests/, and a
    # README that says `python -m pytest`. Bare `pytest` puts tests/ on sys.path, not the root, so a
    # correct fix failed with ModuleNotFoundError and the evidence gate would have blocked it.
    root = tmp_path / "project"
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "ledgerly"\nversion = "1.0"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    )
    write(root, "ledgerly/__init__.py", "def total(amounts):\n    return sum(amounts)\n")
    write(root, "tests/conftest.py", "")
    write(
        root,
        "tests/test_total.py",
        "from ledgerly import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    )
    # The interpreter running this suite has pytest; nothing else may decide the import path.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(executables, "_cache", None)
    for name in ("PYTHONPATH", "PYTEST_ADDOPTS"):
        monkeypatch.delenv(name, raising=False)

    plan = plan_task("Fix the crash when saving settings", root, config, home=home).verification
    unit = plan.get("unit_tests")
    assert unit is not None and unit.mode == "ready"
    [evidence] = execute_plan(plan, root, timeout=120, only={"unit_tests"})
    assert evidence.status == "passed", evidence.output_tail


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


_PYTEST_XDIST = Finding(
    "tool:pytest-xdist",
    "tool",
    "pytest (parallel, pytest-xdist)",
    Status.INSTALLED_UNVERIFIED,
    ["unit_testing", "integration_testing"],
    tool_kind="cli",
)


def test_unit_tests_run_in_parallel_when_pytest_xdist_is_there(config, profiles, registry):
    plan = plan_for(
        "Fix the crash when saving settings", config, profiles, registry, [_PYTEST, _PYTEST_XDIST]
    )
    unit = plan.get("unit_tests")
    assert unit is not None and unit.mode == "ready"
    assert unit.command == ["python", "-m", "pytest", "-n", "auto", "-q"]

    # Through an external environment too, and plain pytest stays the fallback.
    findings = [_PYTEST, _PYTEST_XDIST, _environment(Status.INSTALLED_UNVERIFIED)]
    inv = Inventory(".", "", False, {"python": 3}, findings)
    [rec] = recommend([CapabilityRequirement("unit_testing", "required")], inv, registry)
    assert rec.selected is not None and rec.selected.command == [
        "poetry",
        "run",
        "python",
        "-m",
        "pytest",
        "-n",
        "auto",
        "-q",
    ]
    assert [c.id for c in rec.alternatives][:1] == ["pytest"]


def test_pytest_xdist_is_never_suggested_where_it_is_missing(config, profiles, registry):
    unit = plan_for("Fix the crash when saving settings", config, profiles, registry, [_PYTEST]).get(
        "unit_tests"
    )
    assert unit is not None and unit.command == ["python", "-m", "pytest", "-q"]
    inv = Inventory(".", "", False, {"python": 3}, [])
    assert "pytest-xdist" not in [c.id for c in candidates_for("unit_testing", inv, registry)]


def test_unknown_superseded_tool_is_rejected(tmp_path: Path):
    from susanoo.config import ConfigError
    from susanoo.registry import load_registry

    write(
        tmp_path,
        ".susanoo/tools.toml",
        '[[tools]]\nid = "fast-tests"\nkind = "cli"\ncapabilities = ["unit_testing"]\nsupersedes = ["nope"]\n',
    )
    with pytest.raises(ConfigError, match="supersedes unknown tool 'nope'"):
        load_registry(tmp_path)


def test_timeout_evidence_says_how_to_raise_the_limit(tmp_path: Path):
    plan = VerificationPlan("fast", [_step("slow", [sys.executable, "-c", "import time; time.sleep(5)"])])
    [result] = execute_plan(plan, tmp_path, timeout=0.5)
    assert result.status == "timeout"
    assert "0.5 s" in result.note
    assert "--timeout" in result.note and "[verify] timeout_seconds" in result.note


def test_verify_timeout_comes_from_configuration(python_project: Path, monkeypatch: pytest.MonkeyPatch):
    import io

    from susanoo import cli

    seen: list[float] = []

    def fake_execute(plan, root, *, timeout, only=None, required_only=False):
        seen.append(timeout)
        return []

    monkeypatch.setattr(cli, "execute_plan", fake_execute)

    def verify(*extra: str) -> None:
        args = ["verify", "Fix the crash", "--execute", "--no-record", *extra, "--path", str(python_project)]
        assert cli.main(args, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO()) == 0

    verify()
    write(python_project, ".susanoo/config.toml", "[verify]\ntimeout_seconds = 1800\n")
    verify()
    verify("--timeout", "60")
    assert seen == [900, 1800, 60]


@pytest.mark.parametrize("value", [0, -5, "long", True])
def test_verify_timeout_must_be_a_positive_number(value: object, config):
    from susanoo.config import ConfigError, merge, validate

    with pytest.raises(ConfigError, match=r"\[verify\] timeout_seconds"):
        validate(merge(config, {"verify": {"timeout_seconds": value}}))


@pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="pytest-xdist is not installed")
def test_project_that_declares_pytest_xdist_runs_its_tests_in_parallel(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, config
):
    # The interpreter running this suite has pytest and pytest-xdist; the project declares both.
    root = tmp_path / "project"
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "calc"\nversion = "1.0"\n'
        '[project.optional-dependencies]\ndev = ["pytest", "pytest-xdist"]\n',
    )
    write(root, "calc.py", "def add(a, b):\n    return a + b\n")
    write(
        root, "tests/test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(executables, "_cache", None)
    for name in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_XDIST_AUTO_NUM_WORKERS"):
        monkeypatch.delenv(name, raising=False)

    plan = plan_task("Fix the crash when saving settings", root, config, home=home).verification
    unit = plan.get("unit_tests")
    assert unit is not None and unit.command == ["python", "-m", "pytest", "-n", "auto", "-q"]
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "2")
    [evidence] = execute_plan(plan, root, timeout=120, only={"unit_tests"})
    assert evidence.status == "passed", evidence.output_tail
    assert "bringing up nodes" in evidence.output_tail  # pytest-xdist started its workers
