"""Enforcement gates as pure functions: hook event in, decision out.

Skills are instructions, and a model can drift from them. These gates turn the
non-negotiable parts of a Susanoo workflow into deterministic checks that Claude
Code applies through hooks:

- plan-approval gate: no file edits in STANDARD/DEEP until the user approves the plan;
- approval integrity: ``susanoo approve`` always requires an explicit user prompt;
- irreversible-action gate: risky shell commands ask for confirmation while the
  ``irreversible-action-gate`` control is active;
- hands-off (the default): only the evidence gate applies; Claude Code's own permission
  settings decide what needs confirmation (see ``autonomous``);
- evidence gate: the turn cannot end while required verification lacks evidence;
- compaction recovery: a short route summary is re-injected after context compaction
  (needs a settings hook; Claude Code does not fire compaction events for skill hooks).

Gates are guardrails against drift, not a security boundary against a hostile agent.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from susanoo.config import Config
from susanoo.enforcement.state import RouteState


def _susanoo_command(subcommand: str) -> re.Pattern[str]:
    """Matches `susanoo <sub>`, `susanoo.exe <sub>`, `"susanoo" <sub>`, `python -m susanoo[.cli] <sub>`."""
    return re.compile(rf"susanoo(?:\.exe|\.cli)?[\"']?\s+{subcommand}\b", re.IGNORECASE)


_APPROVE = _susanoo_command("approve")
_OUTCOME = _susanoo_command("outcome")
_ROUTE = _susanoo_command("route")
_ACCEPT_MISSING = re.compile(r"--accept-missing-evidence\b", re.IGNORECASE)
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_GIT_BASH_DRIVE = re.compile(r"^/([a-zA-Z])/")


@dataclass
class Decision:
    """What a hook should tell Claude Code. ``None`` fields mean 'no opinion'."""

    permission: str | None = None  # PreToolUse: "deny" | "ask"
    reason: str = ""
    block_stop: bool = False
    context: str | None = None  # additionalContext for the model
    notes: list[str] = field(default_factory=list)  # internal (logged to enforcement records)


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _matches(command: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if _compile(pattern).search(command):
            return pattern
    return None


def _unquoted(command: str) -> str:
    """The command with quoted arguments removed, so `grep "a->b"` does not look like a redirect."""
    return _QUOTED.sub("''", command)


def _writes(command: str, cfg: dict[str, Any]) -> str | None:
    return _matches(_unquoted(command), list(cfg.get("shell_write_patterns", [])))


def tool_path(file_path: str, root: Path) -> Path:
    """A tool's file path as an absolute path: ``~`` expanded, Git Bash style ``/d/...`` on
    Windows and Windows separators elsewhere converted, relative paths taken from ``root``."""
    raw = os.path.expanduser(file_path)
    raw = (
        _GIT_BASH_DRIVE.sub(lambda m: f"{m.group(1)}:/", raw)
        if os.name == "nt"
        else raw.replace(chr(92), "/")
    )
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _inside(file_path: str, root: Path) -> bool:
    path = tool_path(file_path, root)
    try:
        a = os.path.normcase(os.path.realpath(path))
        b = os.path.normcase(os.path.realpath(root))
    except OSError:
        return True
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def autonomous(event: dict[str, Any], state: RouteState | None, cfg: dict[str, Any]) -> bool:
    """Whether Susanoo works hands-off: always with ``hands_off`` (the default), otherwise when
    Claude Code runs in one of ``autonomous_modes`` (read live from each hook event, so switching
    modes mid-task takes effect immediately).

    Susanoo then adds no approval and no questions of its own; Claude Code's permission settings
    decide. The evidence gate stays, because it never asks the user anything. (Without
    ``hands_off``, a task that asks for autonomy in words is recorded as an approval instead,
    which keeps the irreversible-action gate; see ``susanoo.enforcement.auto``.)
    """
    if cfg.get("hands_off"):
        return True
    return str(event.get("permission_mode", "")) in set(cfg.get("autonomous_modes", []))


def _needs_approval(state: RouteState, cfg: dict[str, Any], hands_off: bool = False) -> bool:
    if hands_off:
        return False
    return state.profile in set(cfg.get("approval_profiles", [])) and not state.approved


def starts_work(event: dict[str, Any], config: Config) -> bool:
    """Whether this tool call changes the project (used to arm the evidence gate)."""
    cfg = config.get("enforcement", {})
    tool = str(event.get("tool_name", ""))
    raw_input = event.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    if tool in set(cfg.get("edit_tools", [])):
        return True
    if tool in ("Bash", "PowerShell"):
        return _writes(str(tool_input.get("command", "")), cfg) is not None
    return False


def summary(state: RouteState, hands_off: bool = False) -> str:
    """Compact reminder of the active route (follow-up prompts, and after compaction)."""
    r = state.route
    if state.approved:
        approval = "Plan approved: yes."
    elif hands_off:
        approval = "Hands-off: no plan approval needed; share the plan and continue."
    else:
        approval = "Plan approved: NO - present the plan and wait for approval before editing."
    parts = [
        f"Susanoo: active route {state.record_id} uses the {state.profile.upper()} workflow "
        f"(risk {r.get('risk', {}).get('level', '?')}, complexity {r.get('complexity', {}).get('level', '?')}).",
        approval,
    ]
    if state.controls:
        parts.append("Mandatory safety controls: " + ", ".join(state.controls) + ".")
    if r.get("phases"):
        parts.append("Phases: " + " -> ".join(str(p) for p in r["phases"]) + ".")
    pending = state.missing + state.failing
    if pending:
        parts.append("Required verification still lacking passing evidence: " + ", ".join(pending) + ".")
    parts.append(
        "Finish with verification evidence and `susanoo outcome latest --status ... --source agent`."
    )
    return " ".join(parts)


def pre_tool_use(event: dict[str, Any], state: RouteState | None, root: Path, config: Config) -> Decision:
    cfg = config.get("enforcement", {})
    tool = str(event.get("tool_name", ""))
    raw_input = event.get("tool_input")
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    if autonomous(event, state, cfg):
        # Hands-off: Claude Code's own permission settings decide; Susanoo asks nothing. Recording
        # "completed" without evidence would defeat the evidence gate, so that is refused outright
        # instead of asked about.
        command = str(tool_input.get("command", "")) if tool in ("Bash", "PowerShell") else ""
        if _APPROVE.search(command):
            # Nothing needs approval here, so this costs no prompt in practice; but an approval
            # Claude recorded itself would still count if the user later sets hands_off = false.
            return Decision(
                permission="ask",
                reason="Susanoo plan approval must come from you. Allow this only if you have reviewed "
                "and accept the plan.",
            )
        if _ACCEPT_MISSING.search(command):
            return Decision(
                permission="deny",
                reason="Susanoo works hands-off here, so a task cannot be recorded as completed without "
                "passing evidence. Run the required checks, or record the outcome as partial.",
            )
        return Decision()

    if tool in ("Bash", "PowerShell"):
        command = str(tool_input.get("command", ""))
        if _APPROVE.search(command):
            return Decision(
                permission="ask",
                reason="Susanoo plan approval must come from you. Allow this only if you have reviewed "
                "and accept the plan.",
            )
        if _ACCEPT_MISSING.search(command):
            return Decision(
                permission="ask",
                reason="This records the task as completed although required verification has no "
                "passing evidence. Allow only if you accept that.",
            )
        if state is None:
            return Decision()
        if _needs_approval(state, cfg):
            if _OUTCOME.search(command):
                return Decision(
                    permission="ask",
                    reason="This closes a Susanoo route whose plan was never approved, which also "
                    "releases its enforcement gates. Allow only if you want to end this task.",
                )
            if _ROUTE.search(command):
                return Decision(
                    permission="ask",
                    reason=f"A new route would replace the unapproved {state.profile.upper()} route "
                    f"{state.record_id}. Allow only if the task really changed.",
                )
        if "irreversible-action-gate" in state.controls:
            hit = _matches(command, list(cfg.get("irreversible_commands", [])))
            if hit:
                return Decision(
                    permission="ask",
                    reason="Susanoo irreversible-action gate: this command can deploy, publish, rewrite "
                    "history, or destroy data. Confirm it is intended.",
                    notes=[f"irreversible pattern: {hit}"],
                )
        if _needs_approval(state, cfg):
            hit = _writes(command, cfg)
            if hit:
                return Decision(
                    permission="ask",
                    reason=f"Susanoo: the {state.profile.upper()} plan has not been approved yet and this "
                    "command may modify files or the environment.",
                    notes=[f"write pattern: {hit}"],
                )
        return Decision()

    if state is None:
        return Decision()
    if tool in set(cfg.get("edit_tools", [])) and _needs_approval(state, cfg):
        file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not file_path or _inside(file_path, root):
            return Decision(
                permission="deny",
                reason=(
                    f"Susanoo plan-approval gate: the {state.profile.upper()} workflow for record "
                    f"{state.record_id} requires the user to approve the plan before files are changed. "
                    "Present the plan, then ask the user to approve it; approval is recorded by running "
                    "`susanoo approve`, which the user must confirm. Investigation (reading, searching) "
                    "is still allowed."
                ),
            )
    return Decision()


def stop(
    event: dict[str, Any],
    state: RouteState | None,
    config: Config,
    prior_blocks: int,
    work_started: bool = True,
) -> Decision:
    """Block the end of the turn while required verification lacks evidence.

    The gate only arms once work has started (a file was changed) and the plan, if one is
    required, is approved; before that, ending a turn to present a plan or ask a question
    is exactly what should happen. ``prior_blocks`` counts consecutive blocks for the same
    unmet set; after ``max_stop_blocks`` the turn may end (the release is logged) so the
    gate can never trap a session that genuinely cannot run a check.
    """
    cfg = config.get("enforcement", {})
    if state is None or state.profile not in set(cfg.get("evidence_profiles", [])):
        return Decision()
    if not work_started or _needs_approval(state, cfg, autonomous(event, state, cfg)):
        return Decision()
    if not state.missing and not state.failing:
        return Decision()
    max_blocks = int(cfg.get("max_stop_blocks", 3))
    if event.get("stop_hook_active") and prior_blocks >= max_blocks:
        return Decision(
            notes=[f"stop allowed after {prior_blocks} blocks; unmet: {state.missing + state.failing}"]
        )
    lines = [f"Susanoo evidence gate ({state.profile.upper()}, record {state.record_id}): not done yet."]
    if state.missing:
        lines.append("Required verification without evidence: " + ", ".join(state.missing) + ".")
    if state.failing:
        lines.append("Required checks whose latest result is not passing: " + ", ".join(state.failing) + ".")
    lines.append(
        "Run `susanoo verify --execute` for the runnable checks. For checks you perform yourself (e.g. "
        "review), record the real result: `susanoo evidence latest --step <id> --status passed|failed|skipped "
        '--note "..."`. If a check cannot be done, record it as skipped with the reason. If the task cannot '
        'be finished, record that: `susanoo outcome latest --status partial --source agent --notes "..."`.'
    )
    return Decision(block_stop=True, reason=" ".join(lines))


def session_start(event: dict[str, Any], state: RouteState | None, config: Config | None = None) -> Decision:
    if state is None or event.get("source") != "compact":
        return Decision()
    cfg = (config or {}).get("enforcement", {})
    return Decision(context=summary(state, autonomous(event, state, cfg)))
