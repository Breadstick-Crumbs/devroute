"""The shipped Claude Code integration files must stay valid and in sync."""

from __future__ import annotations

from pathlib import Path

from susanoo.adapters.claude_code.discovery import parse_frontmatter
from susanoo.adapters.claude_code.installer import targets

REPO = Path(__file__).resolve().parent.parent


def test_templates_have_valid_frontmatter():
    for target in targets(with_agents=True):
        if not target.path.endswith(".md"):
            continue
        meta = parse_frontmatter(target.content)
        assert meta.get("name"), target.path
        assert meta.get("description"), target.path


def test_skill_uses_arguments_and_records_evidence():
    [skill] = [t for t in targets() if t.path.endswith("SKILL.md")]
    meta = parse_frontmatter(skill.content)
    assert meta["name"] == "susanoo"
    assert meta["disable-model-invocation"] == "true"
    assert "$ARGUMENTS" in skill.content
    assert "susanoo route --stdin" in skill.content
    assert "susanoo verify --execute" in skill.content
    # The skill must not pre-approve running project commands.
    assert "susanoo verify" not in meta["allowed-tools"]


def test_reviewer_is_read_only():
    [reviewer] = [t for t in targets() if t.path.endswith("susanoo-reviewer.md")]
    meta = parse_frontmatter(reviewer.content)
    assert "Edit" not in meta["tools"] and "Write" not in meta["tools"]


def test_repository_dogfood_copies_match_templates():
    variants: dict[str, set[str]] = {}
    for enforce in (False, True):
        for target in targets(with_agents=True, enforce=enforce):
            variants.setdefault(target.path, set()).add(target.content)
    for path, contents in variants.items():
        if not path.startswith(".claude/"):
            continue
        repo_copy = REPO / path
        assert repo_copy.is_file(), f"{path} missing; run `susanoo init` in the repository"
        assert repo_copy.read_text(encoding="utf-8") in contents, f"{path} is out of sync; run `susanoo init`"


def test_enforcement_variant_registers_hooks_in_frontmatter():
    from susanoo.adapters.claude_code.installer import skill_template

    plain, enforced = skill_template(False), skill_template(True)
    assert "hooks:" not in plain
    meta = parse_frontmatter(enforced)
    assert meta["name"] == "susanoo" and "hooks" in meta
    frontmatter_end = enforced.index("\n---\n", 3)
    for event in ("pre-tool-use", "stop"):
        assert f"susanoo hook {event}" in enforced[:frontmatter_end]
    # Claude Code 2.1.296 does not fire SessionStart or PostCompact hooks declared by a skill
    # (scripts/live_enforcement_check.py); compaction recovery is a settings snippet instead.
    for event in ("session-start", "post-compact"):
        assert f"susanoo hook {event}" not in enforced
    # Everything except the hook block is identical.
    assert enforced.replace(enforced[enforced.index("hooks:") : frontmatter_end + 1], "") == plain
