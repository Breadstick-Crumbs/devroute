"""Routing of visual-only UI work (ADR 0008, slice 4): no architectural or payment weight for styling,
and payment logic always keeps its controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from susanoo.analyzer import assess
from susanoo.config import ConfigError, load_config
from susanoo.enforcement.auto import compact_brief
from susanoo.pipeline import message_floor, plan_task
from susanoo.render import render_brief
from susanoo.router import load_profiles, route
from tests.conftest import write

PAYMENT_CONTROLS = {"security-review", "data-safety", "external-sandbox"}


def _route(task: str):
    config = load_config()
    a = assess(task, config)
    return a, route(a, load_profiles(), config)


@pytest.mark.parametrize(
    ("task", "ignored"),
    [
        ("Change the checkout button color to green", "payments / billing"),
        ("Make the checkout form layout responsive on small screens", "payments / billing"),
        ("Tighten the padding on the payment form", "payments / billing"),
        ("Recolor the billing page buttons to match the brand", "payments / billing"),
        ("Redesign the pricing page layout for mobile", "architectural change"),
        ("Redesign the onboarding screens in the React Native app", "architectural change"),
    ],
)
def test_visual_edits_drop_payment_and_architecture_weight(task: str, ignored: str):
    a, d = _route(task)
    assert any(p.startswith(ignored) for p in a.presentational)
    assert not {"payments", "architecture-change"} & a.signal_ids()
    assert not PAYMENT_CONTROLS & {c.id for c in d.controls}
    assert d.profile in ("fast", "standard")


@pytest.mark.parametrize(
    "task",
    [
        "Fix the checkout button",  # no look word: may be a submit bug
        "Redesign the checkout page",  # redesign without a look word stays payment work
        "Change the checkout form validation messages",  # validation decides what is submitted
        "Change the checkout button color and the tax rate",  # a second clause with payment logic
        "Change the Stripe checkout button color",  # the provider is named
        "Restyle the checkout page and fix the refund button so it calls Stripe again",
        "Change how the checkout calculates tax",
        "Show the annual billing discount on the pricing page",
        "Rebuild the checkout flow UI with a new multi-step form",
        "Change the checkout button color to show the total",  # a keep word anywhere
        "Make the checkout form layout responsive and submit it to the payment API",
        # Found in review of #51: each of these escaped the first version of the rule.
        "Fix the checkout button on mobile",  # "fix" is a behavior change; "mobile" is not a look
        "Fix the broken payment form on mobile",
        "Fix the payment form fields on mobile",
        "Fix the checkout page on small screens",
        "Fix the checkout form on mobile Safari",
        "Fix the checkout page for logged-out users on mobile",
        "Update the checkout page layout for the new payment provider",
        "Update the checkout form layout for Apple Pay",
        "Restyle the checkout page for Klarna",
        "Update the checkout page for mobile wallets",
        "Update the payment form fields for SEPA on mobile",
        "Change the checkout layout to a single page",
        "Move the checkout form to the mobile app",
        "Move the billing page to the new dashboard layout",
        "Change the billing page layout to show overdue accounts",
        "Change the payment form card fields to plain text inputs",
        "Change the checkout button on mobile to one-click purchase",
        "Change the checkout button color on mobile to disable it",
        "Change the checkout button color in the Stripe-hosted page",  # hyphenated keep words
        "Fix the double-submit checkout button on mobile",
        "Fix the pre-authorization checkout button on mobile",
        "Change the payment page text for non-refundable orders",
        "Make the checkout button green on hover and auto-charge",
        "Change the checkout button color to green and hide on mobile",  # "and" anywhere
        "Change the checkout button color to green and disable on mobile",
        "Change the checkout button color, then the tax label",  # a second clause
        "Change the checkout button color.\nAlso recalculate the order",
    ],
)
def test_payment_logic_keeps_every_payment_control(task: str):
    a, d = _route(task)
    assert "payments" in a.signal_ids()
    assert {c.id for c in d.controls} >= PAYMENT_CONTROLS
    assert d.profile == "deep" and a.risk.level == "critical"


@pytest.mark.parametrize(
    "task",
    [
        "Redesign the database schema behind the orders page",
        "Redesign the frontend state management architecture",
        "Redesign the checkout page data model",
        # Found in review of #51: another architectural phrase or a technology in the tail.
        "Redesign the orders page with microservices",
        "Redesign the product page with server-side rendering",
        "Redesign the settings page to use Redux",
        "Redesign the search page with Elasticsearch",
        "Redesign the orders page with GraphQL",
    ],
)
def test_architectural_redesigns_stay_architectural(task: str):
    a, _ = _route(task)
    assert "architecture-change" in a.signal_ids() and not a.presentational


def test_briefs_say_what_was_ignored(tmp_path: Path, home: Path):
    write(tmp_path, "src/App.tsx", "export const App = () => null;\n")
    plan = plan_task("Change the checkout button color to green", tmp_path, load_config(), home=home)
    assert "Ignored as a purely visual change: payments / billing ('checkout')" in render_brief(plan)
    short = compact_brief(plan, "su-1", True, "susanoo")
    assert "Ignored as a purely visual change" in short and "raise the route" in short


def test_the_users_message_still_sets_the_floor():
    # Claude may describe a payment task in visual words; the user's own message keeps its controls.
    config = load_config()
    floor = message_floor("The checkout button no longer charges the card, fix it", config)
    assert floor is not None
    a = assess("Change the checkout button color to green", config, floor=floor)
    d = route(a, load_profiles(), config, minimum=floor.profile)
    assert {c.id for c in d.controls} >= PAYMENT_CONTROLS


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        (
            '[[assessment.presentational.rules]]\nsignals = ["no-such-signal"]\nverbs = ["change"]\n',
            "unknown signal",
        ),
        ('[[assessment.presentational.rules]]\nsignals = ["payments"]\nverbs = []\n', "verbs"),
        (
            '[[assessment.presentational.rules]]\nsignals = ["payments"]\nverbs = ["x"]\nrequire_appearance = 1\n',
            "require_appearance",
        ),
        # A typo must not silently drop the appearance requirement.
        (
            '[[assessment.presentational.rules]]\nsignals = ["payments"]\nverbs = ["x"]\nrequire-look = true\n',
            "unknown key",
        ),
        ('[assessment.presentational]\nrules = "none"\n', "array of tables"),
        ('[assessment.presentational]\nkeep = ["["]\n', "keep"),
        ("[assessment.presentational]\nmax_words = 500\n", "max_words"),
    ],
)
def test_presentational_configuration_is_validated(tmp_path: Path, toml: str, message: str):
    write(tmp_path, ".susanoo/config.toml", toml)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_presentational_rule_can_be_turned_off(tmp_path: Path):
    write(tmp_path, ".susanoo/config.toml", "[assessment.presentational]\nrules = []\n")
    config = load_config(tmp_path)
    a = assess("Change the checkout button color to green", config)
    assert "payments" in a.signal_ids() and not a.presentational


def test_long_or_tricky_input_is_fast():
    # Review of #51: the first version's regex backtracked exponentially on run-on prose.
    import time

    config = load_config()
    tasks = [
        "Change the checkout button" + " to" * 300 + " zz!x",
        "Redesign the settings page" + " with the new look on the main screen for users" * 300,
    ]
    for task in tasks:
        start = time.perf_counter()
        assess(task, config)
        assert time.perf_counter() - start < 5  # main takes about a second on the longest; no blow-up


@pytest.mark.parametrize(
    ("task", "profile"),
    [
        # New patterns must not lower routes outside UI work (checked against v0.7 behavior).
        ("Reduce the padding in the protobuf message", "standard"),
        ("Increase the margin on the loan calculation", "standard"),
        ("Build the project", "standard"),
        ("Build the Docker image and push it", "standard"),
        ("Add breakpoints to the debugger", "fast"),
    ],
)
def test_new_patterns_stay_out_of_non_ui_work(task: str, profile: str):
    assert _route(task)[1].profile == profile


def test_visual_tweak_caps_never_apply_with_data_changes():
    a, _ = _route("Add a color variant to the button and migrate the DB")
    assert a.complexity.level != "low"
