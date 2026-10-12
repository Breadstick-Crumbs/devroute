from __future__ import annotations

import pytest

from devroute.analyzer import assess
from devroute.config import merge
from devroute.router import route


def decide(task, config, profiles, override=None):
    return route(assess(task, config), profiles, config, override=override)


def test_three_profiles_load_with_phases(profiles):
    assert list(profiles) == ["fast", "standard", "deep"]
    assert all(p.phases for p in profiles.values())
    assert profiles["fast"].approval_checkpoints == []
    assert any(phase.approval for phase in profiles["deep"].phases)


@pytest.mark.parametrize(
    "task, expected",
    [
        ("Fix typo in README", "fast"),
        ("Add pagination to the /users API endpoint", "standard"),
        ("Build a SaaS dashboard with authentication and billing", "deep"),
        ("make it better", "standard"),
    ],
)
def test_recommendations(task, expected, config, profiles):
    assert decide(task, config, profiles).profile == expected


def test_reasons_explain_the_choice(config, profiles):
    d = decide("Build a SaaS dashboard with authentication and billing", config, profiles)
    assert "risk is critical" in d.reasons
    assert "complexity is high" in d.reasons


def test_security_sensitive_small_change_gets_controls(config, profiles):
    d = decide("Change password hashing from SHA1 to bcrypt", config, profiles)
    assert d.profile != "fast"
    assert "security-review" in {c.id for c in d.controls}
    assert any("security-sensitive" in c for c in d.checkpoints)


def test_override_keeps_mandatory_controls(config, profiles):
    d = decide("Change password hashing from SHA1 to bcrypt", config, profiles, override="fast")
    assert d.profile == "fast"
    assert d.overridden
    assert "security-review" in {c.id for c in d.controls}
    assert any("Mandatory safety controls still apply" in n for n in d.override_notes)
    assert any("security-sensitive" in c for c in d.checkpoints)


def test_critical_risk_override_is_raised_to_floor(config, profiles):
    d = decide("Drop the legacy orders table from the production database", config, profiles, override="fast")
    assert d.recommended == "deep"
    assert d.profile == "standard"
    assert any("critical_risk_floor" in n for n in d.override_notes)


def test_floor_is_configurable(config, profiles):
    strict = merge(config, {"routing": {"critical_risk_floor": "deep"}})
    d = decide("Drop the legacy orders table from the production database", strict, profiles, override="fast")
    assert d.profile == "deep"


def test_upward_override_is_allowed(config, profiles):
    d = decide("Fix typo in README", config, profiles, override="deep")
    assert d.profile == "deep" and d.overridden


def test_unknown_override_is_an_error(config, profiles):
    with pytest.raises(ValueError, match="unknown profile"):
        decide("Fix typo in README", config, profiles, override="turbo")


def test_low_confidence_adds_scope_checkpoint(config, profiles):
    d = decide("make it better", config, profiles)
    assert d.checkpoints[0].startswith("Confirm the clarified task scope")
