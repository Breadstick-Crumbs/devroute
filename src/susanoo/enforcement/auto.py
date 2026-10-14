"""Automatic routing: every prompt is classified, and clear coding tasks are routed.

Used by the ``UserPromptSubmit`` hook that ``susanoo init --global`` (or ``--auto`` with the
settings scope) installs. Classification is deterministic and deliberately cautious: when a
prompt is not clearly a task, nothing happens.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from susanoo.config import PROFILE_ORDER, Config
from susanoo.discovery.executables import save_path_cache, use_path_cache
from susanoo.enforcement import gates
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.prompt import AMBIGUOUS, QUESTION, SKIP, TASK
from susanoo.enforcement.state import RouteState
from susanoo.evaluation.store import RecordStore, new_record_id
from susanoo.paths import (
    global_active,
    global_installed,
    global_manifest_path,
    home_state,
    install_manifest_path,
    project_mode,
)
from susanoo.pipeline import TaskPlan, message_floor, plan_task, route_record
from susanoo.selection import EXTENSION_KINDS, installed_extensions
from susanoo.verification.executor import redact

_CLAUSE_START = re.compile(r"(?:^|[,;:.!?–—]\s*|\s-\s|\s(?:and|then|but|so)\s)", re.IGNORECASE)
_QUOTED = re.compile(r"\"[^\"]*\"|'[^'\s][^']*'|`[^`]*`|“[^”]*”|‘[^’]*’")
_CLAUSES = re.compile(r"[,;:.!?–—]|\s-\s")


@lru_cache(maxsize=64)
def _starts(alternatives: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, so "make sure" wins over "make".
    ordered = sorted(alternatives, key=len, reverse=True)
    return re.compile(rf"^(?:{'|'.join(ordered)})(?![\w'-])", re.IGNORECASE)


def _strip_polite(text: str, polite: tuple[str, ...]) -> str:
    pattern = re.compile(rf"^(?:{'|'.join(polite)})(?![\w'-])[\s,!.:;-]*", re.IGNORECASE)
    previous = None
    while previous != text:
        previous, text = text, pattern.sub("", text, count=1)
    return text


def classify(prompt: str, cfg: dict[str, Any]) -> str:
    """``task``, ``question``, ``ambiguous``, or ``skip`` (never routed)."""
    text = prompt.strip()
    if not text or text[0] in "/!#" or text.startswith(str(cfg.get("skip_prefix", "?"))):
        return SKIP
    first = re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)[0].strip()
    core = _strip_polite(first, tuple(cfg.get("polite", [])))
    if _starts(tuple(cfg.get("question_starts", []))).search(core):
        return QUESTION
    if first.endswith("?") and core == first:
        return QUESTION  # "port 8080 is in use, what should I do?"; "Can you add X?" keeps its prefix
    verb = _starts(tuple(cfg.get("task_verbs", []))).search(core)
    if not verb:
        return AMBIGUOUS
    following = core[verb.end() :].lstrip()
    if following and _starts(tuple(cfg.get("not_after_verb", []))).search(following):
        return AMBIGUOUS  # "write me a haiku", "make sense of this", "drop it", "move on"
    return TASK if len(text.split()) >= int(cfg.get("min_words", 3)) else AMBIGUOUS


def wants_autonomy(prompt: str, cfg: dict[str, Any]) -> bool:
    """Whether the prompt asks Claude to work hands-off.

    Only a request counts, not a description of a feature: the phrase must start a clause in the
    first or last clause of the prompt, outside quotes ("add a 'Don't ask again' checkbox" and
    "implement a no questions asked refund flow" do not count).
    """
    text = _QUOTED.sub(lambda m: " " * len(m.group(0)), prompt.strip())
    bounds = [m.end() for m in _CLAUSES.finditer(text)]
    first_end = bounds[0] if bounds else len(text)
    last_start = bounds[-1] if bounds and bounds[-1] < len(text.rstrip()) else 0
    for phrase in cfg.get("autonomy", []):
        pattern = rf"(?:(?:please|just|and|work|proceed|go|continue|run|act|do it|do this|do everything)\s+)*{phrase}"
        for found in re.finditer(rf"(?<![\w-]){pattern}(?![\w-])", text, re.IGNORECASE):
            starts_clause = any(
                m.end() == found.start() for m in _CLAUSE_START.finditer(text, 0, found.start() + 1)
            )
            if starts_clause and (found.start() < first_end or found.start() >= last_start):
                return True
    return False


def _manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def auto_settings(root: Path) -> dict[str, Any]:
    """The install manifest whose automatic routing applies to ``root``, or ``{}``.

    A project's own `--auto` install wins; otherwise the global install applies, also in projects
    that have their own `.susanoo/` setup without automatic routing.
    """
    if project_mode(root):
        manifest = _manifest(install_manifest_path(root))
        if manifest.get("auto") is True:
            return manifest
        if not global_installed() or not (root / ".git").exists():
            return {}
    elif not global_active(root):
        return {}
    manifest = _manifest(global_manifest_path())
    return manifest if manifest.get("auto") is True else {}


def unrouted_note(command: str) -> str:
    """Context for a message that was not routed: Claude decides whether it is a coding task."""
    return (
        "Susanoo did not route this message (it is not clearly a coding task). If handling it means "
        "changing code that is not part of an open Susanoo task, first run "
        f'`{command} route "<the task in one line>"` and follow the brief it prints. Otherwise '
        "ignore this note."
    )


def compact_brief(
    plan: TaskPlan,
    record_id: str,
    hands_off: bool,
    command: str,
    lead: str = "automatically",
    asked_autonomy: bool = False,
) -> str:
    """A short brief for Claude's context: what to do, not every detail of the assessment.

    ``hands_off`` drops the approval check-in. ``asked_autonomy`` (the user asked in words not to be
    asked) also drops the prompt to ask clarifying questions: hands-off is about permission prompts,
    not about guessing what the user meant.
    """
    a, d = plan.assessment, plan.decision
    lines = [
        f"Susanoo routed this task {lead} (record {record_id}).",
        f"Workflow: {d.profile.upper()} (complexity {a.complexity.level}, risk {a.risk.level}, "
        f"uncertainty {a.uncertainty.level}). Why: {'; '.join(d.reasons)}.",
    ]
    if a.negated:
        lines.append("Ignored as out of scope: " + ", ".join(repr(n) for n in a.negated) + ".")
    for control in d.controls:
        lines.append(f"Safety control {control.id}: {control.description}")
    tools = []
    for rec in plan.recommendations:
        if rec.selected is None:
            continue
        extras = installed_extensions(rec)
        if rec.selected.kind in EXTENSION_KINDS:
            tools.append(f"{rec.title}: {rec.selected.name}")
        elif extras:
            tools.append(
                f"{rec.title}: {rec.selected.name}, or the installed " + ", ".join(c.name for c in extras)
            )
    if tools:
        lines.append("Installed tools that fit: " + "; ".join(tools) + ".")
    missing = [f"{g.title} ({g.suggestion})" for g in plan.gaps if g.blocking]
    if missing:
        lines.append(
            "Missing tools: " + "; ".join(missing) + ". Checks that need them are not required here; "
            "mention the gap in the final report. Never install anything without the user's approval."
        )
    if a.confidence == "low" and not asked_autonomy:
        lines.append(
            "Low confidence in this route: " + " ".join(a.confidence_notes) + " Before editing, ask the "
            "user one to three focused questions about scope and expected behavior, unless their message "
            "or the conversation already answers them. These are questions about the task, not an "
            "approval request."
        )
    lines.append("Steps: " + " -> ".join(p.title for p in plan.profile.phases) + ".")
    lines.append(
        "If this route understates the task (data loss, security, payments, production, or a wider change "
        f'than it says), raise it: `{command} raise standard|deep --reason "<the risk you found>"`; risk in '
        "the reason adds its safety controls. Routes are never lowered; only the user picks a lighter workflow."
    )
    required = [s for s in plan.verification.steps if s.level == "required" and s.mode != "unavailable"]
    if required:
        lines.append(
            "Required checks: "
            + "; ".join(
                s.title
                + (f" (`{' '.join(s.command)}`)" if s.command else f" ({s.withheld})" if s.withheld else "")
                + (f" [{s.instructions}]" if s.instructions else "")
                for s in required
            )
            + "."
        )
    if hands_off or d.profile not in ("standard", "deep"):
        lines.append(
            "Do not wait for approval: show the brief in two or three lines, do the task, then verify."
            + (
                ""
                if asked_autonomy
                else " If the request is ambiguous in a way that changes what you build, ask the user "
                "instead of guessing."
            )
            + (
                " The safety controls still apply to how you work; Claude Code's permission settings "
                "handle confirmations."
                if d.controls
                else ""
            )
        )
    else:
        lines.append(
            "Show the brief in two or three lines, investigate read-only, then present a short plan"
            + (
                ""
                if asked_autonomy
                else ", with questions about anything ambiguous that changes what you build,"
            )
            + " and wait for the user's approval (one check-in). File edits are blocked until the user approves; "
            f"approval is recorded with `{command} approve`, which the user confirms."
        )
    lines.append(
        f"Before finishing: run `{command} verify --execute` and fix failures; never claim success "
        f"without passing evidence. Then record the result with `{command} outcome latest --status "
        "completed|partial|abandoned --source agent`."
    )
    return "\n".join(lines)


def autonomy_requested(
    store: RecordStore, route: dict[str, Any], record_id: str, message: dict[str, Any]
) -> bool:
    """Whether the user asked in words not to be asked: on the route, its approvals, or the latest message."""
    if route.get("autonomous") is True or message.get("hands_off") is True:
        return True
    return any(
        a.get("record_id") == record_id and a.get("checkpoint") == "autonomy" for a in store.read("approvals")
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def grant_autonomy(store: RecordStore, record_id: str, session: str) -> None:
    """The user's own words grant autonomy: recorded as their approval of the plan."""
    store.append(
        "approvals",
        {
            "record_id": record_id,
            "approved_at": _now(),
            "checkpoint": "autonomy",
            "session_id": session,
            "note": "the user asked for autonomous work in the prompt",
        },
    )


def _has_unfinished_work(state: RouteState, hook_record: dict[str, Any]) -> bool:
    return bool(hook_record.get("work_started")) and bool(state.missing + state.failing)


def unfinished_work(root: Path, state: RouteState, session: str) -> bool:
    """Whether files were changed under ``state`` and required checks still lack passing evidence."""
    return _has_unfinished_work(state, _session_record(root, session, state.record_id))


def close_superseded(store: RecordStore, state: RouteState, notes: str) -> None:
    """Close a route that a new one replaces, so its gates stop. Not counted as a failure."""
    store.append(
        "outcomes",
        {
            "record_id": state.record_id,
            "recorded_at": _now(),
            "status": "abandoned",
            "source": "susanoo",
            "notes": notes,
            "unmet_verification": sorted(state.missing + state.failing),
        },
    )


def _session_record(root: Path, session: str, record_id: str) -> dict[str, Any]:
    from susanoo.enforcement.hooks import SessionState  # imported late: hooks imports this module

    data = SessionState(root, session).data
    return data if data.get("record_id") == record_id else {}


def _last_activity(store: RecordStore, state: RouteState, hook_record: dict[str, Any]) -> datetime | None:
    stamps = [str(state.route.get("created_at")), str(hook_record.get("last_seen"))]
    stamps += [
        str(a.get("approved_at")) for a in store.read("approvals") if a.get("record_id") == state.record_id
    ]
    times = []
    for stamp in stamps:
        try:
            times.append(datetime.fromisoformat(stamp))
        except ValueError:
            continue
    return max(times) if times else None


def _is_follow_up(prompt: str, state: RouteState, cfg: dict[str, Any], last: datetime | None) -> bool:
    """A prompt continues the open task when it says so, or soon after the task's last activity."""
    if _starts(tuple(cfg.get("continuation", []))).search(prompt.strip()):
        return True
    if last is None:
        return False
    return (datetime.now(UTC) - last).total_seconds() / 60 < float(cfg.get("follow_up_minutes", 20))


def on_prompt(
    event: dict[str, Any],
    root: Path,
    config: Config,
    store: RecordStore,
    state: RouteState | None,
    session: str,
) -> str | None:
    """Additional context for a prompt, or ``None`` to let it pass untouched."""
    settings = auto_settings(root)
    if not settings or not session:
        return None
    cfg = config.get("auto", {})
    prompt = str(event.get("prompt", ""))
    kind = classify(prompt, cfg)
    command = str(settings.get("command") or "susanoo")
    hands_off = wants_autonomy(prompt, cfg)
    # Every message is remembered (as its keyword floor, not its text), so a route Claude makes
    # later in this turn is never lower than the message, and only the user picks a lighter one.
    message = message_floor(prompt, config)
    prompt_state.save(
        root,
        session,
        {
            "at": _now(),
            "id": new_record_id(),  # unique per message (stamps are to the second)
            "kind": kind,
            "floor": message.to_dict() if message else None,
            "hands_off": hands_off,
            "named": prompt_state.named_profiles(prompt),
            # `/susanoo`: the skill routes the user's own words and shows the full brief.
            "skill": bool(re.match(r"/susanoo(?![\w-])", prompt.strip(), re.IGNORECASE)),
            "permission_mode": str(event.get("permission_mode", "")),
        },
    )
    if kind in (QUESTION, AMBIGUOUS):
        return unrouted_note(command)
    if kind != TASK:
        return None
    use_path_cache(home_state() / "cache" / "path-lookups.json")
    try:
        plan = plan_task(prompt, root, config)
    finally:
        save_path_cache()
    higher = state is not None and PROFILE_ORDER.index(plan.decision.profile) > PROFILE_ORDER.index(
        state.profile
    )
    hook_record = _session_record(root, session, state.record_id) if state is not None else {}
    unfinished = state is not None and _has_unfinished_work(state, hook_record)
    if (
        state is not None
        and not higher
        and (unfinished or _is_follow_up(prompt, state, cfg, _last_activity(store, state, hook_record)))
    ):
        # A follow-up to the task in progress: keep its route and remind Claude of it. A task with
        # unfinished work is never closed automatically; Claude is told instead.
        if hands_off and not state.approved:
            grant_autonomy(store, state.record_id, session)
        return (
            "Susanoo: this continues the task in progress."
            + (
                " The user asked for autonomous work: do not wait for approval, and do not stop to ask "
                "clarifying questions."
                if hands_off
                else ""
            )
            + " "
            + gates.summary(state, hands_off or gates.autonomous(event, state, config.get("enforcement", {})))
            + (
                " If this is a separate task, finish the open one or record its outcome first "
                f"(`{command} outcome latest --status partial --source agent`)."
                if unfinished
                else ""
            )
        )
    record_id = new_record_id()
    if state is not None:
        # The open task is superseded (a riskier or unrelated new task): close it, so its gates stop.
        close_superseded(
            store, state, f"superseded by {record_id} (a new task arrived before an outcome was recorded)"
        )
    record = route_record(plan, record_id, config, session)
    if record.get("task"):
        record["task"] = redact(str(record["task"]))[:1000]
    record.update({"auto": True, "autonomous": hands_off})
    store.append("routes", record)
    if hands_off:
        grant_autonomy(store, record_id, session)
    live_hands_off = hands_off or gates.autonomous(event, None, config.get("enforcement", {}))
    return compact_brief(plan, record_id, live_hands_off, command, asked_autonomy=hands_off)
