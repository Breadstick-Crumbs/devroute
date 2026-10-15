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
        ("Redesign the pricing page layout for mobile", "architectural change"),
        ("Redesign the onboarding screens in the React Native app", "architectural change"),
    ],
)
def test_visual_redesigns_drop_architectural_weight(task: str, ignored: str):
    a, d = _route(task)
    assert any(p.startswith(ignored) for p in a.presentational)
    assert not {"payments", "architecture-change"} & a.signal_ids()
    assert not PAYMENT_CONTROLS & {c.id for c in d.controls}
    assert d.profile in ("fast", "standard")


@pytest.mark.parametrize(
    "task",
    [
        # A heuristic never removes payment controls, not even for styling (user decision, 2026-10-15).
        "Change the checkout button color to green",
        "Make the checkout form layout responsive on small screens",
        "Tighten the padding on the payment form",
        "Recolor the billing page buttons to match the brand",
        "Restyle the checkout page",
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
        # Found in the second review of #51: allowed words that still change the flow or card entry.
        "Change the payment form card fields to text fields",
        "Make the payment form card fields text fields",
        "Make the card fields on the payment form text fields",
        "Change the checkout layout to a modal",
        "Change the cart modal to the checkout page layout",
        "Change the checkout page layout into a web view",
        "Make the checkout page a compact modal",
        "Make the checkout button text link to the web checkout page",
        "Make the white checkout button link to the web checkout page",
        "Change the checkout layout to the web layout on the ios app",
        "Change the payment form layout to a modal on mobile",
        "Restyle the checkout page into a modal",
        # Third review of #51: words that sound visual but change what is collected or shown.
        "Make the payment forms consistent",
        "Make the payment form fields consistent",
        "Make the checkout pages consistent",
        "Make the checkout form compact",
        "Make the checkout form layout compact",
        "Make the checkout form compact on mobile",
        "Increase the payments page size",
        "Change the payments page size",
        "Make the checkout page size smaller",
        "Change the checkout fee label copy",
        "Change the billing cancellation section wording",
        "Change the checkout autorenew section wording",
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
        # Second review of #51: platform or framework moves phrased as a redesign.
        "Redesign the react native screens into web views",
        "Redesign the ios app screens into web views",
        "Redesign the react components into web components",
        "Redesign the page components into web components",
        "Redesign the web views into ios views",
        # Third review of #51: the page-name slot is a closed list, so architecture words stay.
        "Redesign the container components",
        "Redesign the client components",
        "Redesign the store components",
        "Redesign the micro-frontend pages",
        "Redesign the micro-frontend layout",
        "Redesign the SSR pages",
        "Redesign the SPA pages",
        "Redesign the RSC components",
        "Redesign the headless components",
        "Redesign the routing layout",
        "Redesign the shell layout",
        "Redesign the module layouts",
        "Redesign the Angular components",
        "Redesign the Vue components",
    ],
)
def test_architectural_redesigns_stay_architectural(task: str):
    a, _ = _route(task)
    assert "architecture-change" in a.signal_ids() and not a.presentational


def test_briefs_say_what_was_ignored(tmp_path: Path, home: Path):
    write(tmp_path, "src/App.tsx", "export const App = () => null;\n")
    plan = plan_task("Redesign the pricing page layout for mobile", tmp_path, load_config(), home=home)
    assert "Ignored as a purely visual change: architectural change ('redesign')" in render_brief(plan)
    short = compact_brief(plan, "su-1", True, "susanoo")
    assert "Ignored as a purely visual change" in short and "raise the route" in short


def test_the_users_message_still_sets_the_floor():
    # Claude may describe an architectural task in visual words; the user's message keeps it DEEP.
    config = load_config()
    floor = message_floor(
        "Refactor the monolith into microservices for orders, inventory, and shipping", config
    )
    assert floor is not None
    a = assess("Redesign the orders page", config, floor=floor)
    d = route(a, load_profiles(), config, minimum=floor.profile)
    assert d.profile == "deep"


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
    a = assess("Redesign the pricing page layout for mobile", config)
    assert "architecture-change" in a.signal_ids() and not a.presentational


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


@pytest.mark.parametrize(
    "task",
    [
        "Redesign the onboarding cards",
        "Redesign the settings tabs",
        "Redesign the dialogs",
        "Redesign the profile menu",
        "Redesign the hero section",
        "Redesign the icons",
        "Redesign the tooltips",
    ],
)
def test_a_visual_redesign_is_at_least_standard(task: str):
    a, d = _route(task)
    assert d.profile == "standard" and a.presentational


@pytest.mark.parametrize(
    "task",
    [
        "Adjust the spacing on the dashboard page and implement real-time sync with conflict resolution",
        "Add size variants to the button component and build a distributed job queue with retries and "
        "exactly-once delivery",
        "Implement style variants for the card component and the concurrent job scheduler",
        "Reduce the padding in the settings page and add multi-tenant isolation to the permissions model",
        "Add size variants to the Button component plus a websocket notification system",
    ],
)
def test_visual_tweak_caps_never_lighten_a_second_clause(task: str):
    assert _route(task)[1].profile == "deep"


@pytest.mark.parametrize(
    "task", ["Fix the API timeouts for mobile clients", "The API is unresponsive on mobile networks"]
)
def test_mobile_clients_and_networks_are_not_responsive_layout(task: str):
    assert "responsive-layout" not in _route(task)[0].signal_ids()


def test_unknown_presentational_keys_are_errors(tmp_path: Path):
    write(tmp_path, ".susanoo/config.toml", '[assessment.presentational]\nkeeps = ["ledger"]\n')
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(tmp_path)
