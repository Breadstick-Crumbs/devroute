"""The shipped Claude Code integration files must stay valid and in sync."""

from __future__ import annotations

from pathlib import Path

from devroute.adapters.claude_code.discovery import parse_frontmatter
from devroute.adapters.claude_code.installer import targets

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
    assert meta["name"] == "devroute"
    assert meta["disable-model-invocation"] == "true"
    assert "$ARGUMENTS" in skill.content
    assert "devroute route --stdin" in skill.content
    assert "devroute verify --execute" in skill.content
    # The skill must not pre-approve running project commands.
    assert "devroute verify" not in meta["allowed-tools"]


def test_reviewer_is_read_only():
    [reviewer] = [t for t in targets() if t.path.endswith("devroute-reviewer.md")]
    meta = parse_frontmatter(reviewer.content)
    assert "Edit" not in meta["tools"] and "Write" not in meta["tools"]


def test_repository_dogfood_copies_match_templates():
    for target in targets(with_agents=True):
        if not target.path.startswith(".claude/"):
            continue
        repo_copy = REPO / target.path
        assert repo_copy.is_file(), f"{target.path} missing; run `devroute init` in the repository"
        assert repo_copy.read_text(encoding="utf-8") == target.content, f"{target.path} is out of sync"
