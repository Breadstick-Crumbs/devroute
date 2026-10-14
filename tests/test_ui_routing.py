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
        ('[[assessment.presentational.rules]]\nsignals = ["payments"]\nverbs = ["("]\n', "verbs"),
        (
            '[[assessment.presentational.rules]]\nsignals = ["payments"]\nverbs = ["x"]\nrequire_look = 1\n',
            "require_look",
        ),
        ('[assessment.presentational]\nkeep = ["["]\n', "keep"),
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
