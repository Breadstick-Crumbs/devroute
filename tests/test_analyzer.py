from __future__ import annotations

import copy

import pytest

from susanoo.analyzer import ProjectContext, assess
from susanoo.analyzer.engine import negated_spans
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


# --- Negation and test-only scope (#11) ------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "Fix the typo in the dashboard title without touching the database",
        "Rename the helper but do not change the database schema",
        "Rename the helper but don’t change the database schema",  # curly apostrophe
        "Fix the typo on the settings page and leave the database untouched",
        "Fix the settings page typo, no schema changes",
    ],
)
def test_negated_phrases_do_not_count_as_signals(config, task: str):
    a = assess(task, config)
    assert "database" not in a.signal_ids()
    assert a.negated and a.dimensions["data_integrity"].level == 0


def test_negation_is_narrow_so_real_risk_is_kept(config):
    # "without breaking" says payments must keep working: still payments work.
    a = assess("Fix the checkout total rounding without breaking existing payments", config)
    assert "payments" in a.signal_ids() and not a.negated


def test_negation_with_another_clause_is_ignored(config):
    # The rest of the task is not a single trivial clause, so nothing is ignored.
    a = assess(
        "Restyle the report page without touching the CSS, and add a column to the users table", config
    )
    assert not a.negated and "database" in a.signal_ids()


def test_negation_needs_a_code_location(config):
    # "its output" is a preservation requirement, not a part of the code base to leave alone.
    a = assess("Refactor the report builder without changing its output", config)
    assert not a.negated


def test_negation_target_is_short(config):
    a = assess("Do this without changing the button color on the long settings page in the database", config)
    assert "database" in a.signal_ids()


def test_test_only_task_caps_runtime_risk(config):
    a = assess("Write unit tests for the invoice total calculator", config)
    assert "payments" in a.signal_ids()  # still known: it needs payment test tooling
    assert a.dimensions["data_integrity"].level <= 1 and a.dimensions["security_privacy"].level <= 1
    assert any("capped" in r for r in a.dimensions["data_integrity"].reasons)


@pytest.mark.parametrize(
    "task",
    [
        "Add tests for the billing proration logic and fix the proration rounding bug",
        "Write tests for the payment webhook and update the retry handler",
        "Add tests for the invoice export and added validation for refunds",
    ],
)
def test_test_only_caps_do_not_apply_with_another_change(config, task: str):
    a = assess(task, config)
    assert a.dimensions["data_integrity"].level == 3
    assert any("not capped" in r for r in a.dimensions["data_integrity"].reasons)


def test_plain_test_request_is_capped(config):
    a = assess("Add tests for the invoice address formatter", config)
    assert "payments" in a.signal_ids()
    assert a.dimensions["data_integrity"].level <= 1


# Tasks where negation or test-only caps would hide real risk if they were too broad. The new
# logic may only lower risk where the text really is a scope exclusion or a pure test request.
ADVERSARIAL = [
    # test requests that also do something risky
    "Write tests and then ship to production",
    "Add tests and truncate the staging tables",
    "Add tests then purge stale invoices",
    "Write tests for the billing export, wipe old invoices",
    "Increase coverage for the payment module and release it",
    "Write tests and seed the production database",
    "Write tests and publish the SDK",
    "Write tests and roll back the failed release in production",
    "Write tests and grant admins billing permissions",
    "Write tests and expose the payments API publicly",
    "Write tests and wire up the stripe webhook",
    "Add tests and bump the stripe dependency",
    "Write tests for the auth middleware and harden it",
    "Improve tests and rework the session handling",
    "Write tests and hook up the auth provider",
    "Add unit tests for the tenant filter and enforce it on every query",
    "Add tests for the payments webhook using live stripe keys",
    "Write integration tests that run against the production database",
    # descriptions of behavior, not instructions
    "Saving the profile form is not changing the password hash",
    "The nightly cron is not affecting the stripe subscriptions as expected",
    "The cleanup job does not touch expired sessions in redis",
    "Settings save does not modify the tenant isolation rules",
    "Investigate why the toggle is not modifying user permissions",
    # a negation followed by risky work in the next clause
    "Don't touch the UI — drop the users table",
    "Don't touch the frontend - truncate the sessions table",
    "Do not change the CSS ( drop the users table )",
    "Do not touch the header & purge all customer records",
    "Without editing the database delete user data for GDPR requests",
    "Without altering the schema we will truncate the audit log table",
    "Without changing anything else drop the deprecated columns",
    "Don't touch the production database config",
    # preservation requirements, not exclusions
    "The migration must not change existing invoices",
    "Upgrade bcrypt without altering password hashing",
    "Not affecting the password hashing, upgrade bcrypt",
    "Do not alter production DNS records",
    "Ship v2 of the library with no breaking changes",
    # negation running into the next sentence or line
    "Do not change the CSS. Stripe billing code needs a new proration path",
    "Don't edit the docs. Auth session code must expire tokens after 15 minutes",
    "Never touch the README. Encryption key code rotates keys nightly",
    "Don't change the docs\nStripe billing code needs a proration rework",
    "Without changing the e.g. payment code, add retries",
    # a location noun added to a preservation requirement, with no low-risk work left
    "Without modifying the payment webhook handler code, add idempotency keys",
    "Keep the session handling code as is but extend token lifetime to 30 days",
    "Upgrade bcrypt without touching the password hashing code",
    "Without changing the auth code, upgrade bcrypt",
    "Without touching the tables, migrate the data",
    "Don't touch the tenant isolation layer",
    "Do not modify the access control code",
    "Keep the permissions table unchanged",
    "Do not change the auth tests",
    "Don't touch the auth code and delete all user records older than a year",
    # a test request that also does something else
    "Write tests for the auth module by disabling CSRF checks",
    "Write tests for the users table by deleting all user records first",
    "Write integration tests for checkout on the shared customer database",
    "Write tests for the payment flow in the real customer database",
    "Add integration tests for refunds hitting real Stripe accounts",
    "Add tests for the password reset flow in prod-like data",
    # a UI or "small" keyword next to plainly described risky work
    "Without touching the encryption code, show full card numbers on the receipt page",
    "Without changing the auth module, render the reset token in the page footer",
    "Don't change the access control module; make the reports page public",
    "Without touching the permissions code, show the admin panel to all users",
    "Update the layout without touching the database schema, and store user SSNs in plain text",
    "Without changing the billing module, add a form to issue account credits",
    "Without modifying the payment webhook handler code, add idempotency keys and a status page",
    "Do not change the db schema; add a react modal that lists customer phone numbers",
    "Without editing the payments module, add a one-line fix to skip signature verification",
    "Without touching the auth code, add a comment and disable the token check",
    "Without touching the auth code, let users delete other users' comments",
    "Fix the button typo without touching the auth code and extend token lifetime",
    "Extend token lifetime and fix the typo without touching the auth code",
    "Extend token lifetime. Fix the typo without touching the auth code",
    # test requests with hyphenated or imperative extras
    "Write tests for prod-like auth data",
    "Write tests for live-mode payment refunds",
    "Write tests for auth remove csrf checks",
]


def _without_new_logic(config):
    plain = copy.deepcopy(config)
    plain["assessment"]["negation"] = {}
    for signal in plain["signals"]:
        signal.pop("caps_only_if", None)
        if signal["id"] == "tests-only":
            signal.pop("caps", None)
    return plain


@pytest.mark.parametrize("task", ADVERSARIAL)
def test_new_logic_never_hides_risk_in_adversarial_tasks(config, task: str):
    before = assess(task, _without_new_logic(config))
    after = assess(task, config)
    for dim in config["risk"]["dimensions"]:
        assert after.level(dim) >= before.level(dim), (dim, after.negated)
    assert after.risk.score >= before.risk.score


def test_pure_test_request_keeps_sandbox_and_production_controls(config):
    # Security and data risk drop for a pure test request, but the dimensions behind the
    # external-sandbox and irreversible-action controls are never capped.
    task = "Write integration tests for the Stripe checkout"
    before, after = assess(task, _without_new_logic(config)), assess(task, config)
    assert after.level("data_integrity") < before.level("data_integrity")
    assert after.level("external_dependencies") == before.level("external_dependencies") == 3
    assert after.level("irreversibility") == before.level("irreversibility")


def test_negation_needs_low_risk_work_to_remain(config):
    # Nothing low-risk explains the task outside the exclusion, so nothing is ignored.
    a = assess("Without modifying the payment webhook handler code, add idempotency keys", config)
    assert not a.negated and "payments" in a.signal_ids()
    a = assess("Don't touch the auth code and delete all user records older than a year", config)
    assert not a.negated and a.risk.level == "critical"


def test_negation_target_stops_at_a_sentence_end(config):
    negation = config["assessment"]["negation"]
    text = "Do not change the CSS. Stripe billing code needs work"
    assert [text[s:e] for s, e in negated_spans(text, negation)] == ["Do not change the CSS"]
    a = assess("Fix the header typo and do not change the CSS.\nStripe billing code is next", config)
    assert not a.negated and "payments" in a.signal_ids()  # a second sentence: nothing ignored
