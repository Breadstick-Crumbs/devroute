"""UI domains and checklists (ADR 0008, slice 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from susanoo.config import ConfigError, load_config
from susanoo.discovery import build_inventory
from susanoo.enforcement.auto import compact_brief
from susanoo.pipeline import plan_task, route_record
from susanoo.render import render_brief
from susanoo.ui import choose_domain, detect_domains, domain_ids, load_ui_catalog
from tests.conftest import write


def _project(root: Path, dependencies: dict[str, str] | None = None) -> Path:
    write(root, "package.json", {"dependencies": dependencies or {}})
    write(root, "src/App.tsx", "export const App = () => null;\n")
    return root


def test_catalog_is_short_ascii_and_sourced():
    catalog = load_ui_catalog()
    assert domain_ids() == ["mobile", "component-library", "ecommerce", "dashboard", "website"]
    for checklist in (catalog.baseline, *catalog.domains):
        assert 5 <= len(checklist.checklist) <= 8, checklist.title
        assert checklist.focus and checklist.sources
        text = " ".join((*checklist.checklist, checklist.focus, checklist.sources, checklist.title))
        assert text.isascii(), checklist.title  # briefs are plain ASCII


@pytest.mark.parametrize(
    ("task", "domain"),
    [
        ("Redesign the pricing page layout for mobile", "website"),  # "for mobile" is not a mobile app
        ("Rebuild the checkout flow UI with a new multi-step form", "ecommerce"),
        ("Add a sortable, filterable orders table to the admin dashboard", "dashboard"),
        ("Redesign the onboarding screens in the React Native app", "mobile"),
        ("Add a size variant to the Button in our design system", "component-library"),
        ("Build a marketing landing page with a hero section", "website"),
        ("Change the primary button color to blue", None),
    ],
)
def test_task_words_choose_the_domain(task: str, domain: str | None):
    assert choose_domain(task, load_config(), []).domain == domain


def test_domain_order_config_then_task_then_project(tmp_path: Path):
    detected = [{"id": "dashboard", "evidence": "recharts"}]
    from_project = choose_domain("Change the primary button color to blue", load_config(), detected)
    assert from_project.domain == "dashboard" and "detected from recharts" in from_project.reason
    from_task = choose_domain("Restyle the checkout page", load_config(), detected)
    assert from_task.domain == "ecommerce" and 'mentions "checkout"' in from_task.reason
    write(tmp_path, ".susanoo/config.toml", '[ui]\ndomain = "website"\n')
    configured = choose_domain("Restyle the checkout page", load_config(tmp_path), detected)
    assert configured.domain == "website" and configured.reason == "set in [ui] domain"
    write(tmp_path, ".susanoo/config.toml", '[ui]\ndomain = "none"\n')
    generic = choose_domain("Restyle the checkout page", load_config(tmp_path), detected)
    assert generic.domain is None and generic.checklist is None and generic.baseline.checklist


@pytest.mark.parametrize(
    ("packages", "files", "expected"),
    [
        ({"@tanstack/react-table"}, (), ["dashboard"]),
        ({"@shopify/hydrogen"}, (), ["ecommerce"]),  # a scope prefix
        ({"@storybook/react", "react"}, (), ["component-library"]),
        ({"@storybook/react", "next"}, (), []),  # Storybook inside an app is not a library
        ({"astro"}, (), ["website"]),
        (set(), ("pubspec.yaml",), ["mobile"]),
        ({"react-native", "recharts"}, (), ["mobile", "dashboard"]),
    ],
)
def test_project_detection(packages: set[str], files: tuple[str, ...], expected: list[str]):
    found = detect_domains(packages, lambda f: f in files)
    assert [d["id"] for d in found] == expected


def test_inventory_detects_domains_read_only(tmp_path: Path, registry, home: Path):
    root = _project(tmp_path, {"recharts": "2.12.0"})
    inventory = build_inventory(root, registry, home=home)
    assert inventory.ui_domains == [{"id": "dashboard", "evidence": "recharts"}]


def test_briefs_show_domain_and_checklists(tmp_path: Path, home: Path):
    root = _project(tmp_path, {"recharts": "2.12.0"})
    plan = plan_task("Add a small size variant to the Button component", root, load_config(), home=home)
    assert plan.ui is not None and plan.ui.domain == "dashboard"
    full = render_brief(plan)
    assert "## UI" in full and "UI domain: Dashboard or data-heavy app (detected from recharts" in full
    assert "Text contrast at least 4.5:1" in full and "Summary first, then drill down" in full
    assert "not peer-reviewed research" in full
    short = compact_brief(plan, "su-1", True, "susanoo")
    line = next(x for x in short.splitlines() if x.startswith("UI domain:"))
    assert (
        "dashboard (detected from recharts" in line
        and "contrast 4.5:1" in line
        and "headline metrics" in line
    )
    assert route_record(plan, "r1", load_config())["ui_domain"] == "dashboard"
    assert plan.to_dict()["ui"]["domain"] == "dashboard"


def test_non_ui_tasks_have_no_ui_section(tmp_path: Path, home: Path):
    plan = plan_task("Add retries to the webhook handler", _project(tmp_path), load_config(), home=home)
    assert plan.ui is None and "## UI" not in render_brief(plan)
    assert route_record(plan, "r1", load_config())["ui_domain"] is None


def test_performance_budget_only_for_websites(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    website = plan_task("Redesign the pricing page layout for mobile", root, load_config(), home=home)
    step = website.verification.get("performance_budget")
    assert step is not None and step.level == "recommended"
    dashboard = plan_task(
        "Add a sortable, filterable orders table to the admin dashboard", root, load_config(), home=home
    )
    assert dashboard.verification.get("performance_budget") is None
    rule = next(r for r in load_config()["verification"] if r["id"] == "performance_budget")
    assert "required" not in rule["levels"].values()  # never required (ADR 0008)


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[ui]\ndomain = "saas"\n', r"\[ui\] domain"),
        (
            '[[verification]]\nid = "x"\ncapability = "visual_check"\nui_domains = ["saas"]\n',
            "unknown UI domain",
        ),
    ],
)
def test_ui_configuration_is_validated(tmp_path: Path, toml: str, message: str):
    write(tmp_path, ".susanoo/config.toml", toml)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)
