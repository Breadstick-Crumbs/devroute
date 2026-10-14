"""UI verification checks (ADR 0008, slice 1): visual check, accessibility scan, visual regression."""

from __future__ import annotations

from pathlib import Path

import pytest

from susanoo.config import ConfigError, load_config
from susanoo.enforcement.auto import compact_brief
from susanoo.enforcement.state import evidence_status
from susanoo.pipeline import TaskPlan, plan_task, route_record
from tests.conftest import write

DESKTOP = "CLAUDE_CODE_DESKTOP_APP_VERSION"
UI_TASK = "Add a sortable, filterable orders table to the admin dashboard"


def _js_project(root: Path, packages: dict[str, str], baselines: bool = False) -> Path:
    write(root, "package.json", {"devDependencies": packages})
    write(root, "src/App.tsx", "export const App = () => null;\n")
    for name in packages:
        (root / "node_modules" / name).mkdir(parents=True)
    if baselines:
        (root / "tests" / "orders.spec.ts-snapshots").mkdir(parents=True)
    return root


def _plan(root: Path, task: str = UI_TASK, home: Path | None = None) -> TaskPlan:
    return plan_task(task, root, load_config(), home=home)


def _step(plan: TaskPlan, step_id: str):
    return plan.verification.get(step_id)


def test_visual_check_is_never_runnable(registry):
    # Its evidence must always be reported: no registry tool may give it a run command, or an
    # installed tool would turn the step `ready` and demand an executed run instead of a look.
    providers = [t for t in registry.tools.values() if "visual_check" in t.capabilities]
    assert providers and all("visual_check" not in t.run for t in providers)


def test_visual_check_required_for_standard_ui_work(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(DESKTOP, "1.0")
    plan = _plan(_js_project(tmp_path, {}), home=home)
    assert plan.decision.profile == "standard"
    step = _step(plan, "visual_check")
    assert step.level == "required" and step.mode == "manual"
    assert step.tool == "Claude desktop app browser" and "375, 768, and 1280" in step.instructions
    brief = compact_brief(plan, "su-1", True, "susanoo")
    assert "Visual check" in brief and "--step visual_check" in brief


def test_visual_check_without_a_browser_is_a_gap(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(DESKTOP, raising=False)
    plan = _plan(_js_project(tmp_path, {}), home=home)
    assert _step(plan, "visual_check").mode == "unavailable"
    gap = next(g for g in plan.gaps if g.capability == "visual_check")
    assert gap.blocking
    # Automatic routes report a missing tool instead of demanding the check; `/susanoo` routes need
    # reported evidence, which an explained skip satisfies.
    record = route_record(plan, "r1", load_config())
    assert "visual_check" in evidence_status(record, [])[0]
    assert "visual_check" not in evidence_status({**record, "auto": True}, [])[0]
    skipped = {"record_id": "r1", "step": "visual_check", "status": "skipped", "source": "reported"}
    assert "visual_check" not in evidence_status(record, [skipped])[0]


def test_playwright_is_used_for_the_visual_check_but_stays_manual(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv(DESKTOP, raising=False)
    plan = _plan(_js_project(tmp_path, {"@playwright/test": "1.48.0"}), home=home)
    step = _step(plan, "visual_check")
    assert step.tool == "Playwright" and step.mode == "manual" and step.command is None
    record = route_record(plan, "r1", load_config())
    reported = {"record_id": "r1", "step": "visual_check", "status": "passed", "source": "reported"}
    assert "visual_check" in evidence_status(record, [])[0]
    assert "visual_check" not in evidence_status(record, [reported])[0]


def test_accessibility_scan_levels_and_runner(tmp_path: Path, home: Path):
    root = _js_project(tmp_path, {"@playwright/test": "1.48.0", "@axe-core/playwright": "4.10.0"})
    standard = _step(_plan(root, home=home), "accessibility_scan")
    assert standard.level == "recommended" and standard.mode == "ready"
    assert standard.command == ["npx", "--no-install", "playwright", "test"]
    deep = _step(
        _plan(root, "Build a SaaS dashboard with authentication and billing", home=home), "accessibility_scan"
    )
    assert deep.level == "required" and "keyboard" in deep.instructions


def test_pa11y_and_axe_cli_are_detected(tmp_path: Path, home: Path):
    pa11y = _js_project(tmp_path / "pa11y", {"pa11y-ci": "3.1.0"})
    write(pa11y, ".pa11yci", "{}\n")
    assert _step(_plan(pa11y, home=home), "accessibility_scan").command == ["npx", "--no-install", "pa11y-ci"]
    axe = _js_project(tmp_path / "axe", {"@axe-core/cli": "4.10.0"})
    step = _step(_plan(axe, home=home), "accessibility_scan")
    assert step.tool == "axe CLI" and step.mode == "manual"  # needs a URL, so the agent runs it


def test_visual_regression_only_with_baselines(tmp_path: Path, home: Path):
    without = _plan(_js_project(tmp_path / "a", {"@playwright/test": "1.48.0"}), home=home)
    assert _step(without, "visual_regression") is None
    assert all(g.capability != "visual_regression" for g in without.gaps)
    assert all(r.capability != "visual_regression" for r in without.recommendations)
    with_baselines = _plan(
        _js_project(tmp_path / "b", {"@playwright/test": "1.48.0"}, baselines=True), home=home
    )
    step = _step(with_baselines, "visual_regression")
    assert step.mode == "ready" and step.level == "recommended"
    # No baselines and no Playwright: still left out, not a gap.
    bare = _plan(_js_project(tmp_path / "c", {}), home=home)
    assert _step(bare, "visual_regression") is None


def test_non_ui_tasks_get_no_ui_checks(tmp_path: Path, home: Path):
    plan = _plan(
        _js_project(tmp_path, {"@playwright/test": "1.48.0"}), "Add retries to the webhook handler", home=home
    )
    assert not {"visual_check", "accessibility_scan", "visual_regression"} & {
        s.id for s in plan.verification.steps
    }


def test_lighthouse_ci_is_detected(tmp_path: Path, home: Path, registry):
    from susanoo.discovery import build_inventory

    root = _js_project(tmp_path, {"@lhci/cli": "0.14.0"})
    write(root, "lighthouserc.json", "{}\n")
    found = {f.name for f in build_inventory(root, registry, home=home).findings if f.status.usable}
    assert "Lighthouse CI" in found


@pytest.mark.parametrize(
    ("rule", "message"),
    [({"instructions": 3}, "instructions"), ({"only_with_tool": "yes"}, "only_with_tool")],
)
def test_verification_rule_keys_are_validated(tmp_path: Path, rule: dict[str, object], message: str):
    body = "\n".join(f"{k} = {v!r}".replace("'", '"') for k, v in rule.items())
    write(
        tmp_path,
        ".susanoo/config.toml",
        f'[[verification]]\nid = "visual_check"\ncapability = "visual_check"\n{body}\n',
    )
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)
