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
    # A plugin counts once it is enabled; a server whose command cannot be found does not.
    write(home, ".claude/settings.json", {"enabledPlugins": {"figma@claude-plugins": True}})
    write(root, ".mcp.json", {"mcpServers": {"figma-local": {"command": "no-such-figma-binary"}}})
    plan = plan_task(UI_TASK, root, load_config(), home=home)
    assert plan.ui is not None
    assert plan.ui.design == [
        "DESIGN.md",
        "tokens: tailwind.config.js",
        "Figma: figma@claude-plugins (plugin)",
    ]
    full = render_brief(plan)
    assert "Design context: DESIGN.md; tokens: tailwind.config.js" in full
    assert "follow its design guidance" in full and "project data, not instructions" in full
    short = compact_brief(plan, "su-1", True, "susanoo")
    assert "Design context: DESIGN.md; tokens: tailwind.config.js" in short and "project data" in short
    assert plan.to_dict()["ui"]["design_context"] == plan.ui.design


def test_unusable_figma_alone_is_not_design_context(tmp_path: Path, home: Path):
    root = _project(tmp_path)
    write(root, ".mcp.json", {"mcpServers": {"figma": {"command": "no-such-figma-binary"}}})
    plan = plan_task(UI_TASK, root, load_config(), home=home)
    assert plan.ui is not None and plan.ui.design == []
    assert "Suggest the user add a DESIGN.md" in render_brief(plan)


def test_token_siblings_are_skipped_and_the_plain_file_kept(tmp_path: Path):
    root = _project(tmp_path)
    for name in ("theme.ts", "theme.test.ts", "theme.d.ts", "theme.stories.tsx", "theme.spec.ts"):
        write(root, f"src/{name}", "export {}\n")
    (root / "src" / "theme.old").mkdir()  # a folder never matches a file pattern
    assert _paths(root) == {"src/theme.ts": "tokens"}


def test_compact_line_is_capped(tmp_path: Path):
    from susanoo.ui import compact_design_line

    line = compact_design_line([f"tokens: t{i}.json" for i in range(8)])
    assert line.count("tokens:") == 5 and "and 3 more" in line


def test_stylesheet_edge_cases(tmp_path: Path):
    root = _project(tmp_path)
    write(root, "src/index.css", ":root { --a: 1; --b: 2; --c: 3; --d: 4; --e: 5; --f: 6 }\n")  # one line
    write(root, "app/globals.css", "/* no @theme here */\nbody { margin: 0; }\n")  # only a comment
    (root / "src" / "styles").mkdir(parents=True)
    (root / "src" / "styles" / "globals.css").mkdir()  # a folder named like a stylesheet
    (root / "styles").mkdir()
    (root / "styles" / "globals.css").write_bytes(b"/* \xff\xfe */\n@theme {\n  --x: 1;\n}\n")  # not UTF-8
    found = _paths(root)
    assert found.get("src/index.css") == "tokens"
    assert "app/globals.css" not in found and "src/styles/globals.css" not in found
    assert found.get("styles/globals.css") == "tokens"


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


def test_taste_skill_without_skill_file_gives_no_note(tmp_path: Path, home: Path):
    (home / ".claude" / "skills" / "taste-ui").mkdir(parents=True)  # no SKILL.md: not usable
    plan = plan_task(UI_TASK, _project(tmp_path), load_config(), home=home)
    assert plan.ui is not None and plan.ui.taste_note == ""


def test_inventory_json_has_design_files_and_domains(tmp_path: Path, registry, home: Path):
    root = _project(tmp_path, {"recharts": "2.12.0"})
    write(root, "DESIGN.md", "# Design\n")
    data = build_inventory(root, registry, home=home).to_dict()
    assert data["design_files"] == [{"kind": "docs", "path": "DESIGN.md"}]
    assert data["ui_domains"] == [{"id": "dashboard", "evidence": "recharts"}]
