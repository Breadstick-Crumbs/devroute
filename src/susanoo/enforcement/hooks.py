"""`susanoo hook <event>`: the bridge between Claude Code hooks and the gates.

Reads the hook event JSON from stdin and writes the response Claude Code expects.
Failures never block work: an internal error exits 1 (Claude Code shows a non-blocking
"hook error" notice) instead of 2 (which would block the action).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo.config import load_config
from susanoo.enforcement import gates
from susanoo.enforcement.state import RouteState, active_route
from susanoo.evaluation.store import RecordStore
from susanoo.paths import find_project_root, is_initialized, records_dir

HOOK_EVENTS = ("pre-tool-use", "stop", "session-start", "post-compact")


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
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:80] or "no-session"
        self.path = records_dir(root) / "hooks" / f"{safe}.json"
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
    if state is None:
        # Approval integrity applies even without an active route.
        if event_name == "pre-tool-use":
            decision = gates.pre_tool_use(event, None, root, config)
            return 0, _pre_tool_use_output(decision)
        return 0, ""

    hook_state = SessionState(root, session)
    record = hook_state.for_record(state.record_id)

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
