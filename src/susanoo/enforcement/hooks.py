"""`susanoo hook <event>`: the bridge between Claude Code hooks and the gates.

Reads the hook event JSON from stdin and writes the response Claude Code expects.
Failures never block work: an internal error exits 1 (Claude Code shows a non-blocking
"hook error" notice) instead of 2 (which would block the action).
"""

from __future__ import annotations

import fnmatch
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo.config import load_config
from susanoo.enforcement import gates
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.prompt import session_file
from susanoo.enforcement.state import RouteState, active_route
from susanoo.evaluation.store import RecordStore
from susanoo.paths import find_project_root, is_initialized

HOOK_EVENTS = ("pre-tool-use", "stop", "session-start", "post-compact", "user-prompt-submit")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _project_root(event: dict[str, Any]) -> Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
    return find_project_root(Path(str(base)))


class SessionState:
    """Per-session hook bookkeeping (work started, stop-gate counter, compaction marker).

    One file per session, so concurrent sessions never reset each other's counters.
    """

    def __init__(self, root: Path, session: str) -> None:
        self.path = session_file(root, session)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        self.data: dict[str, Any] = data if isinstance(data, dict) else {}

    def for_record(self, record_id: str) -> dict[str, Any]:
        """State scoped to one route; switching routes starts fresh."""
        if self.data.get("record_id") != record_id:
            self.data = {"record_id": record_id}
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def _log(store: RecordStore, state: RouteState | None, event_name: str, decision: gates.Decision) -> None:
    if decision.permission or decision.block_stop or decision.notes:
        store.append(
            "enforcement",
            {
                "record_id": state.record_id if state else None,
                "recorded_at": _now(),
                "hook": event_name,
                "permission": decision.permission,
                "blocked_stop": decision.block_stop,
                "notes": decision.notes,
            },
        )


def _exempt(file_path: str, root: Path, config: dict[str, Any]) -> bool:
    """Whether ``file_path`` matches `[auto] route_before_edit_exempt` (agent memory and settings)."""
    patterns = config.get("auto", {}).get("route_before_edit_exempt", [])
    path = Path(os.path.expanduser(file_path))
    try:
        absolute = os.path.realpath(path if path.is_absolute() else root / path)
        relative = Path(os.path.relpath(absolute, os.path.realpath(root))).as_posix()
    except (OSError, ValueError):
        return False
    return any(fnmatch.fnmatch(relative, str(pattern)) for pattern in patterns)


def _route_first(
    event: dict[str, Any], root: Path, config: dict[str, Any], store: RecordStore, session: str
) -> gates.Decision | None:
    """Route before the first edit: with automatic routing, a message that was not routed (it got
    the Susanoo note) and leads to an edit of a project file, with no route open, gets that first
    edit denied once, so Claude routes the task and its gates apply. A retry goes through, so an
    edit that is not part of a coding task is only delayed. It never asks the user anything, so it
    applies in every permission mode. Edits made through shell commands are not covered.
    """
    if not session or not config.get("auto", {}).get("route_before_edit", True):
        return None
    if str(event.get("tool_name", "")) not in set(config.get("enforcement", {}).get("edit_tools", [])):
        return None
    raw_input = event.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not file_path or not gates._inside(file_path, root) or _exempt(file_path, root, config):
        return None
    message = prompt_state.load(root, session)
    # Only messages that got the note ("question" and "ambiguous" in the classifier); a task was
    # routed by the hook, and "?" or a slash command asked for no routing.
    if not message or message.get("kind") not in ("question", "ambiguous"):
        return None
    at = str(message.get("at", ""))
    marker = str(message.get("id") or at)
    if not at or prompt_state.nudged(root, session, marker):
        return None
    if any(
        r.get("session_id") == session and str(r.get("created_at", "")) >= at for r in store.read("routes")
    ):
        return None  # Claude routed after this message (and that route is closed by now)
    from susanoo.enforcement.auto import auto_settings  # heavy; only reached for this edit

    settings = auto_settings(root)
    if not settings:
        return None  # automatic routing was turned off after the message
    if not prompt_state.mark_nudged(root, session, marker):
        return None  # without the mark a retry would be denied again: fail open instead
    command = str(settings.get("command") or "susanoo")
    return gates.Decision(
        permission="deny",
        reason=(
            "Susanoo: this message was not routed, and this edit changes a project file. If you are working "
            f'on a coding task, run `{command} route "<the task in one line>"` first, follow the brief it '
            "prints, then retry the edit. If this edit is not part of a coding task, retry it as it is: it "
            "will go through."
        ),
        notes=["route before edit: first edit for an unrouted message denied once"],
    )


def handle(event_name: str, event: dict[str, Any]) -> tuple[int, str]:
    """Return (exit_code, stdout) for one hook invocation."""
    if event_name not in HOOK_EVENTS:
        raise ValueError(f"unknown hook event '{event_name}' (expected one of {', '.join(HOOK_EVENTS)})")
    root = _project_root(event)
    if not is_initialized(root):
        return 0, ""
    config = load_config(root)
    store = RecordStore(root)
    session = str(event.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    state = active_route(store, session)
    if event_name == "user-prompt-submit":
        from susanoo.enforcement import auto  # heavy (routing pipeline); only prompts need it

        context = auto.on_prompt(event, root, config, store, state, session)
        if not context:
            return 0, ""
        return 0, json.dumps(
            {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
        )
    if state is None:
        if event_name == "pre-tool-use":
            first = _route_first(event, root, config, store, session)
            if first is not None:
                _log(store, None, event_name, first)
                return 0, _pre_tool_use_output(first)
            # Approval integrity applies even without an active route.
            decision = gates.pre_tool_use(event, None, root, config)
            return 0, _pre_tool_use_output(decision)
        return 0, ""

    hook_state = SessionState(root, session)
    record = hook_state.for_record(state.record_id)
    record["last_seen"] = _now()  # automatic routing measures follow-ups from the last activity

    if event_name == "pre-tool-use":
        decision = gates.pre_tool_use(event, state, root, config)
        if decision.permission != "deny" and gates.starts_work(event, config):
            record["work_started"] = True
        if record.pop("compacted", False):
            # PostCompact cannot add context itself; the next tool call carries the reminder.
            decision.context = gates.summary(state)
            decision.notes.append("route summary re-injected on the first tool call after compaction")
        hook_state.save()
        _log(store, state, event_name, decision)
        return 0, _pre_tool_use_output(decision)

    if event_name == "stop":
        unmet = sorted(state.missing + state.failing)
        prior = int(record.get("stop_blocks", 0)) if record.get("unmet") == unmet else 0
        decision = gates.stop(event, state, config, prior, work_started=bool(record.get("work_started")))
        if decision.block_stop:
            record.update({"unmet": unmet, "stop_blocks": prior + 1})
        else:
            record.pop("stop_blocks", None)
            record.pop("unmet", None)
        hook_state.save()
        _log(store, state, event_name, decision)
        if decision.block_stop:
            return 0, json.dumps({"decision": "block", "reason": decision.reason})
        return 0, ""

    if event_name == "session-start":
        decision = gates.session_start(event, state)
        if not decision.context:
            return 0, ""
        record.pop("compacted", None)
        hook_state.save()
        _log(
            store,
            state,
            event_name,
            gates.Decision(notes=["route summary re-injected at session start (compact)"]),
        )
        output = {"hookEventName": "SessionStart", "additionalContext": decision.context}
        return 0, json.dumps({"hookSpecificOutput": output})

    # post-compact: only reachable from a user-added settings hook, since Claude Code does not
    # fire PostCompact for skill hooks. The skill's PreToolUse hook then carries the summary.
    record["compacted"] = True
    hook_state.save()
    _log(store, state, event_name, gates.Decision(notes=["compaction observed"]))
    return 0, ""


def _pre_tool_use_output(decision: gates.Decision) -> str:
    output: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if decision.permission:
        output["permissionDecision"] = decision.permission
        output["permissionDecisionReason"] = decision.reason
    if decision.context:
        output["additionalContext"] = decision.context
    return json.dumps({"hookSpecificOutput": output}) if len(output) > 1 else ""
