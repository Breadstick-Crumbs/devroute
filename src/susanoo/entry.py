"""Console entry point, with a fast path for `susanoo hook`.

Hooks run on every prompt and tool call (in global mode, in every project), so `susanoo hook`
imports only what it needs: it first checks whether Susanoo is active for the folder, using path
helpers alone, and exits immediately when it is not. The full CLI is only loaded for commands.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path


def _project_has_own_hooks(root: Path) -> bool:
    """Whether the project's settings run Susanoo hooks of its own (not the global ones)."""
    try:
        text = (root / ".claude" / "settings.local.json").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"susanoo(?:\.exe)?\s+hook\s+(?!--global)[a-z]", text) is not None


def run_hook(args: Sequence[str]) -> int:
    from_global = "--global" in args
    events = [a for a in args if not a.startswith("-")]
    if len(events) != 1:
        print("usage: susanoo hook [--global] <event>", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # not ours to judge; never block Claude Code on bad input
    if not isinstance(event, dict):
        return 0

    from susanoo.paths import find_project_root, is_initialized, project_mode

    root = find_project_root(
        Path(str(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()))
    )
    if not is_initialized(root):
        return 0
    if from_global and project_mode(root) and _project_has_own_hooks(root):
        return 0  # the project's own hooks act; running both would double every gate

    from susanoo.config import ConfigError
    from susanoo.enforcement.hooks import handle

    try:
        code, output = handle(events[0], event)
    except ConfigError as exc:
        # A broken configuration must not put an error on every prompt; `susanoo doctor` reports it.
        print(f"susanoo: configuration error, hooks skipped: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:  # fail open (non-blocking) with one line, never a traceback
        print(f"susanoo: hook error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if output:
        print(output)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["hook"]:
        return run_hook(args[1:])
    from susanoo.cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
