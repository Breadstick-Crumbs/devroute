"""Claude Code adapter: environment discovery and project integration files."""

from susanoo.adapters.claude_code.discovery import (
    discover_claude_code,
    infer_capabilities,
    parse_frontmatter,
)
from susanoo.adapters.claude_code.installer import (
    FileAction,
    apply_install,
    apply_uninstall,
    plan_install,
    plan_uninstall,
    skill_template,
)

__all__ = [
    "FileAction",
    "apply_install",
    "apply_uninstall",
    "discover_claude_code",
    "infer_capabilities",
    "parse_frontmatter",
    "plan_install",
    "plan_uninstall",
    "skill_template",
]
