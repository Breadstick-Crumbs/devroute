from __future__ import annotations

import pytest

from susanoo.analyzer import ProjectContext, assess
from susanoo.config import DIMENSIONS


def test_all_ten_dimensions_are_scored(config):
    a = assess("Add pagination to the /users API endpoint", config)
    assert set(a.dimensions) == set(DIMENSIONS)
    assert all(0 <= d.level <= 3 for d in a.dimensions.values())


def test_trivial_change_is_low_everything(config):
    a = assess("Fix typo in README", config)
    assert (a.complexity.level, a.risk.level, a.uncertainty.level) == ("low", "low", "low")
    assert a.dimensions["implementation_complexity"].level == 0
    assert any("capped" in r for r in a.dimensions["implementation_complexity"].reasons)


def test_complexity_and_risk_are_independent(config):
    """A small change with severe security implications must not look routine."""
    a = assess("Change one line in the password hashing function", config)
    assert a.complexity.level == "low"
    assert a.risk.level in ("high", "critical")
    assert a.dimensions["security_privacy"].level == 3


def test_complex_task_is_high_complexity_and_critical_risk(config):
    a = assess(
        "Build a property management platform with authentication, payments, and an admin dashboard", config
    )
    assert a.complexity.level == "high"
    assert a.risk.level == "critical"
    assert set(a.areas) >= {"authentication", "payments", "user interface"}
    assert a.dimensions["parallelization"].level >= 2


def test_single_severe_risk_dimension_is_not_averaged_away(config):
    a = assess("Rotate the API keys used by the export job", config)
    assert a.dimensions["security_privacy"].level == 3
    assert a.risk.level in ("high", "critical")


def test_two_severe_risk_dimensions_make_risk_critical(config):
    a = assess("Drop the legacy orders table from the production database", config)
    assert a.dimensions["data_integrity"].level == 3
    assert a.dimensions["irreversibility"].level == 3
    assert a.risk.level == "critical"


def test_vague_task_reports_low_confidence_and_uncertainty(config):
    a = assess("make it better", config)
    assert a.confidence == "low"
    assert a.uncertainty.level in ("moderate", "high")
    assert a.confidence_notes


def test_intermittent_bug_raises_uncertainty(config):
    a = assess("Investigate why the date formatting test fails intermittently", config)
    assert a.dimensions["technical_uncertainty"].level == 3
    assert a.uncertainty.level == "high"


def test_scope_signal_disables_small_change_caps(config):
    a = assess("Rewrite the README generator and fix typos", config)
    assert a.dimensions["architectural_complexity"].level == 3


def test_patterns_match_whole_words_only(config):
    a = assess("Update the product page copy", config)
    assert "production" not in a.signal_ids()
    assert all(s.id != "production" for s in a.signals)


def test_assessment_is_deterministic(config):
    task = "Add SSO login with OAuth for enterprise customers"
    assert assess(task, config).to_dict() == assess(task, config).to_dict()


def test_requirements_come_from_signals(config):
    a = assess("Add Stripe checkout", config)
    reqs = {r.capability: r.level for r in a.requirements}
    assert reqs["payment_sandbox"] == "required"
    assert reqs["security_analysis"] == "required"


def test_project_context_adjusts_and_explains(config):
    plain = assess("Add a settings page", config)
    ctx = assess("Add a settings page", config, ProjectContext(has_test_framework=False, is_git_repo=False))
    assert ctx.dimensions["testing_difficulty"].level == plain.dimensions["testing_difficulty"].level + 1
    assert ctx.dimensions["irreversibility"].level == plain.dimensions["irreversibility"].level + 1
    assert len(ctx.context_notes) == 2


def test_empty_task_is_rejected(config):
    with pytest.raises(ValueError):
        assess("   ", config)
