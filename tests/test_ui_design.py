"""Design context in UI briefs (ADR 0008, slice 3): found read-only, named, never created."""

from __future__ import annotations

from pathlib import Path

from susanoo.config import load_config
from susanoo.discovery import build_inventory
from susanoo.enforcement.auto import compact_brief
from susanoo.pipeline import plan_task
from susanoo.render import render_brief, render_inventory
from susanoo.ui import detect_design_files
from tests.conftest import write

UI_TASK = "Redesign the pricing page layout for mobile"
TASTE = "Visual direction: follow the taste-ui skill"


def _project(root: Path, packages: dict[str, str] | None = None) -> Path:
    write(root, "package.json", {"devDependencies": packages or {}})
    write(root, "src/App.tsx", "export const App = () => null;\n")
    return root


def _paths(root: Path, packages: set[str] | None = None) -> dict[str, str]:
    return {f["path"]: f["kind"] for f in detect_design_files(root, packages or set())}


def test_design_files_are_found(tmp_path: Path):
    root = _project(tmp_path)
    write(root, "DESIGN.md", "# Design\n")
    write(root, "docs/design-system.md", "# System\n")
    write(root, "tailwind.config.ts", "export default {}\n")
    write(root, "brand.tokens.json", "{}\n")
    (root / ".storybook").mkdir()
    assert _paths(root) == {
        "DESIGN.md": "docs",
        "docs/design-system.md": "docs",
        "tailwind.config.ts": "tokens",
        "brand.tokens.json": "tokens",
        ".storybook/": "storybook",
    }


def test_stylesheets_count_only_when_they_define_a_theme(tmp_path: Path):
    root = _project(tmp_path)
    write(root, "src/index.css", "body { margin: 0; }\n:root { --a: 1; --b: 2; }\n")
    write(root, "app/globals.css", '@import "tailwindcss";\n@theme {\n  --color-brand: #f00;\n}\n')
    props = "\n".join(f"  --space-{i}: {i}px;" for i in range(6))
    write(root, "src/styles/variables.css", f":root {{\n{props}\n}}\n")
    found = _paths(root)
    assert "src/index.css" not in found  # two custom properties are not a token set
    assert found["app/globals.css"] == "tokens" and found["src/styles/variables.css"] == "tokens"


def test_folder_patterns_and_storybook_from_dependencies(tmp_path: Path):
    root = _project(tmp_path)
    write(root, "tokens", "a file, not a token folder\n")
    assert "tokens/" not in _paths(root)
    assert _paths(root, {"@storybook/react", "react"}) == {"package.json (@storybook/react)": "storybook"}


def test_brief_names_design_context_and_tells_claude_to_follow_it(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    write(root, "DESIGN.md", "# Design\n")
    write(root, "tailwind.config.js", "module.exports = {}\n")
    write(root, ".mcp.json", {"mcpServers": {"figma": {"command": "npx", "args": ["figma-mcp"]}}})
    plan = plan_task(UI_TASK, root, load_config(), home=home)
    assert plan.ui is not None
    assert plan.ui.design[:2] == ["DESIGN.md", "tokens: tailwind.config.js"]
    assert any(item.startswith("Figma: figma (MCP server") for item in plan.ui.design)
    full = render_brief(plan)
    assert (
        "Design context: DESIGN.md; tokens: tailwind.config.js" in full and "Read it before designing" in full
    )
    short = compact_brief(plan, "su-1", True, "susanoo")
    assert "Design context: DESIGN.md, tokens: tailwind.config.js" in short and "read it first" in short
    assert plan.to_dict()["ui"]["design_context"] == plan.ui.design


def test_missing_design_context_is_a_suggestion_never_a_file(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    plan = plan_task(UI_TASK, root, load_config(), home=home)
    assert plan.ui is not None and plan.ui.design == []
    assert "Suggest the user add a DESIGN.md" in render_brief(plan)
    assert "suggest the user add a DESIGN.md (never create it yourself)" in compact_brief(
        plan, "su-1", True, "susanoo"
    )
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_taste_skill_is_named_only_when_installed(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    plain = plan_task(UI_TASK, root, load_config(), home=home)
    assert plain.ui is not None and not plain.ui.taste_note and TASTE not in render_brief(plain)
    write(
        home,
        ".claude/skills/taste-ui/SKILL.md",
        "---\nname: taste-ui\ndescription: Design direction from the user's taste profile.\n---\nBody\n",
    )
    plan = plan_task(UI_TASK, root, load_config(), home=home)
    assert plan.ui is not None and plan.ui.taste_note.startswith(TASTE)
    assert "Project DESIGN.md, design tokens and client brand guidelines win" in plan.ui.taste_note
    assert TASTE in render_brief(plan) and TASTE in compact_brief(plan, "su-1", True, "susanoo")
    assert plan.to_dict()["ui"]["taste"] is True


def test_non_ui_tasks_get_no_design_lines(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    write(root, "DESIGN.md", "# Design\n")
    plan = plan_task("Add retries to the webhook handler", root, load_config(), home=home)
    assert plan.ui is None
    assert "Design context" not in render_brief(plan)
    assert "Design context" not in compact_brief(plan, "su-1", True, "susanoo")


def test_inventory_lists_design_files(tmp_path: Path, registry, home: Path):
    root = _project(tmp_path)
    write(root, "DESIGN.md", "# Design\n")
    inventory = build_inventory(root, registry, home=home)
    assert inventory.design_files == [{"kind": "docs", "path": "DESIGN.md"}]
    assert "Design context: DESIGN.md (docs)" in render_inventory(inventory)
