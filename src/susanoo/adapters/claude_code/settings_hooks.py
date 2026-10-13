"""Settings-scoped enforcement: Susanoo's hooks in ``.claude/settings.local.json``.

With ``susanoo init --enforce --enforce-scope settings`` the hooks live in the project's local
settings instead of the skill's frontmatter. Claude Code then runs them in every session in the
project, including sessions resumed in a new process, and fires ``SessionStart`` for compaction
recovery. ``susanoo hook`` does nothing in sessions without an active route.

Only hook handlers whose command starts with ``susanoo hook`` are Susanoo's. Everything else in
the file is preserved. A file that is not valid JSON, has an unexpected shape, or is a symbolic
link is never rewritten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from susanoo.adapters.claude_code.installer import UNREADABLE, FileAction, _atomic_write, _read
from susanoo.config import read_data_text

SETTINGS_FILE = ".claude/settings.local.json"
OWNED_PREFIX = "susanoo hook "
GATE_EVENTS = frozenset({"PreToolUse", "Stop"})  # the events the skill scope also declares


def settings_hooks() -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = json.loads(read_data_text("templates", "settings-hooks.json"))[
        "hooks"
    ]
    return data


def _owned(handler: Any) -> bool:
    command = handler.get("command") if isinstance(handler, dict) else None
    return isinstance(command, str) and command.strip().startswith(OWNED_PREFIX)


def _group_owned(group: Any) -> bool:
    handlers = group.get("hooks") if isinstance(group, dict) else None
    return isinstance(handlers, list) and any(_owned(h) for h in handlers)


def load_settings(path: Path, *, for_write: bool = True) -> tuple[dict[str, Any] | None, str | None]:
    """(parsed settings, or None if absent; problem that forbids rewriting it, or None).

    Symbolic links are refused only for writing; reading follows them, as Claude Code does.
    """
    if for_write and path.is_symlink():
        return None, "a symbolic link"
    text = _read(path)
    if text is None:
        return None, None
    if text == UNREADABLE:
        return None, "not a readable text file"
    try:
        data = json.loads(text.removeprefix("﻿")) if text.strip() else {}
    except json.JSONDecodeError:
        return None, "not valid JSON"
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    shape_ok = isinstance(hooks, dict) and all(
        isinstance(groups, list)
        and all(isinstance(g, dict) and isinstance(g.get("hooks", []), list) for g in groups)
        for groups in hooks.values()
    )
    if not isinstance(data, dict) or not shape_ok:
        return None, "an unexpected structure"
    return data, None


def owned_events(settings: dict[str, Any]) -> set[str]:
    """Hook events in parsed (validated) settings that have a Susanoo handler."""
    hooks = settings.get("hooks", {})
    return {event for event, groups in hooks.items() if any(_group_owned(g) for g in groups)}


def owned_events_in(path: Path) -> set[str]:
    """Hook events with a Susanoo handler in a settings file (empty if absent or not parseable)."""
    data, problem = load_settings(path, for_write=False)
    return owned_events(data) if data is not None and not problem else set()


def without_owned(settings: dict[str, Any], keep: frozenset[str] = frozenset()) -> dict[str, Any]:
    """A copy without Susanoo's handlers, except in ``keep`` events.

    Only events that contain a Susanoo handler change; groups and events emptied by the removal
    are dropped, and everything else is kept exactly as it was.
    """
    result = dict(settings)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return result
    kept: dict[str, Any] = {}
    for event, groups in hooks.items():
        if event in keep or not any(_group_owned(g) for g in groups):
            kept[event] = groups
            continue
        new_groups = []
        for group in groups:
            if _group_owned(group):
                remaining = [h for h in group["hooks"] if not _owned(h)]
                if remaining:
                    new_groups.append({**group, "hooks": remaining})
            else:
                new_groups.append(group)
        if new_groups:
            kept[event] = new_groups
    if kept:
        result["hooks"] = kept
    else:
        result.pop("hooks", None)
    return result


def with_owned(settings: dict[str, Any]) -> dict[str, Any]:
    result = without_owned(settings)
    hooks = dict(result.get("hooks") or {})
    for event, groups in settings_hooks().items():
        hooks[event] = [*hooks.get(event, []), *groups]
    result["hooks"] = hooks
    return result


def _target(data: dict[str, Any], want: bool, keep: frozenset[str]) -> dict[str, Any]:
    return with_owned(data) if want else without_owned(data, keep)


def plan_settings(
    root: Path,
    *,
    want: bool,
    managed: bool,
    created: bool = False,
    keep: frozenset[str] = frozenset(),
) -> FileAction | None:
    """Plan adding (``want``) or removing Susanoo's hooks.

    Removal only happens when Susanoo manages the file (``managed``), so a user's own
    `susanoo hook` entries are never removed by a skill-scope install. ``keep`` lists events
    whose Susanoo handlers stay on removal; ``created`` means Susanoo created the file.
    """
    if not want and not managed:
        return None
    data, problem = load_settings(root / SETTINGS_FILE)
    if problem:
        return FileAction(SETTINGS_FILE, "conflict", f"is {problem}; left untouched")
    if data is None:
        return FileAction(SETTINGS_FILE, "create", "enforcement hooks") if want else None
    target = _target(data, want, keep)
    if target == data:
        return FileAction(SETTINGS_FILE, "unchanged") if want else None
    if want:
        return FileAction(SETTINGS_FILE, "update", "enforcement hooks added; other settings kept")
    if not target and created:
        return FileAction(SETTINGS_FILE, "remove", "only Susanoo's hooks were in it")
    return FileAction(SETTINGS_FILE, "remove-hooks", "Susanoo's hooks removed; other settings kept")


def apply_settings(
    root: Path,
    *,
    want: bool,
    managed: bool,
    created: bool = False,
    keep: frozenset[str] = frozenset(),
) -> tuple[FileAction | None, bool]:
    """Re-plan against the current file and apply; return (what was done, Susanoo created it).

    Raises ``OSError`` if the file cannot be written.
    """
    action = plan_settings(root, want=want, managed=managed, created=created, keep=keep)
    if action is None or action.action in ("conflict", "unchanged"):
        return action, created
    path = root / SETTINGS_FILE
    data, _ = load_settings(path)
    if action.action == "remove":
        path.unlink(missing_ok=True)
        parent = path.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        return action, False
    _atomic_write(path, json.dumps(_target(data or {}, want, keep), indent=2, ensure_ascii=False) + "\n")
    return action, created or action.action == "create"
