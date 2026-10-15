"""Token usage and activity time of a routed task, read from Claude Code session transcripts.

Opt-in with ``[records] capture_usage = true``. Claude Code keeps one transcript per session at
``<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<project>/<session>.jsonl``, with subagent
transcripts in ``<session>/subagents/``. Each assistant message line carries the API usage
counts and a timestamp.

Only lines that look like assistant or user messages are decoded. From assistant messages only
the ``usage`` counts, the ``timestamp``, the message and request ids (to count a message once),
``costUSD`` (present in older transcript formats), and the ``type``, ``id``, and ``name`` of
tool-use blocks (to count tool calls by tool) are read. From user messages in the session's own
transcript only the ``timestamp``, ``uuid`` (to count a line once), ``isMeta`` and
``isCompactSummary``, the block ``type`` and ``tool_use_id`` (to tell a tool result from a prompt,
and which tool it came from), ``origin.kind`` (to tell a prompt you typed from one the app sent), and
``permissionDecision.source`` (to tell a tool call you approved from one your settings allowed)
are read, to split the task's time. Message text and tool inputs and results are never read or
stored. Decoding user messages (tool results can be large) has its own time budget, and its time
does not count against the capture's: past it, the split is left out and tokens are captured as
before. The transcript format is not a documented interface, so
anything unexpected (missing files, changed fields, unreadable lines) means nothing is
captured, never an error.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from susanoo.adapters.claude_code.global_install import claude_home
from susanoo.config import Config
from susanoo.evaluation.report import TIME_PARTS, TOKEN_FIELDS
from susanoo.evaluation.store import RecordStore

CAPTURED_FIELDS = (
    "tokens",
    "token_counts",
    "duration_minutes",
    "cost_usd",
    "tool_calls",
    "time_minutes",
    "tool_minutes",
    "user_prompts",
    "approvals",
)
_TOOL_BLOCKS = ("tool_use", "server_tool_use")
_MAX_TOOL_NAMES = 200
_USER_LINE = re.compile(rb'"type"\s*:\s*"user"')
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CHUNK = 1 << 20
# Transcripts are read from the end and reading stops this far before the route was created,
# so a long session costs only the part of it the route covers.
_SLACK = timedelta(minutes=5)
# Capture also runs in the prompt hook: past this much reading, nothing is captured.
_BUDGET_BYTES = 64 << 20
_BUDGET_SECONDS = 2.0
# Decoding user lines for the time split: past this, the split is left out.
_SPLIT_SECONDS = 0.5


class _OverBudget(Exception):
    pass


@dataclass
class Usage:
    counts: dict[str, int]
    messages: int
    last_at: datetime
    cost_usd: float | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)
    # Seconds per part of TIME_PARTS, and the prompts you typed and permission prompts you
    # answered, from the session's own transcript; None when it has no line in the window.
    time_split: dict[str, float] | None = None
    tool_time: dict[str, float] | None = None  # seconds of the tools and approvals parts, per tool
    user_prompts: int | None = None
    approvals: int | None = None

    @property
    def tokens(self) -> int:
        return sum(self.counts.values())


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def transcript_files(session_id: str, home: Path | None = None) -> list[Path]:
    """The session's transcript and its subagents' transcripts (empty when not found)."""
    if not _SESSION_ID.fullmatch(session_id):
        return []
    files: list[Path] = []
    for main in sorted(((home or claude_home()) / "projects").glob(f"*/{session_id}.jsonl")):
        files.append(main)
        files += sorted((main.parent / session_id / "subagents").glob("*.jsonl"))
    return files


def _lines_from_end(path: Path) -> Generator[bytes, None, None]:
    with path.open("rb") as fh:
        position = fh.seek(0, os.SEEK_END)
        rest = b""
        while position > 0:
            size = min(_CHUNK, position)
            position -= size
            fh.seek(position)
            lines = (fh.read(size) + rest).split(b"\n")
            rest = lines[0]
            yield from reversed(lines[1:])
        yield rest


@dataclass
class _Entry:
    key: object
    time: datetime
    counts: dict[str, int]
    cost: float | None
    tools: list[tuple[str, str]]  # (tool-use id, tool name)


def _tools(content: object) -> list[tuple[str, str]]:
    """Ids and names of the tool calls in a message's content blocks; nothing else is read."""
    tools: list[tuple[str, str]] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") in _TOOL_BLOCKS:
            tool_id, name = block.get("id"), block.get("name")
            if isinstance(tool_id, str) and isinstance(name, str) and _TOOL_NAME.fullmatch(name):
                tools.append((tool_id, name))
    return tools


def _decode(line: bytes) -> tuple[dict[str, Any], datetime] | None:
    """A timestamped assistant or user message line and its time, else None."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") not in ("assistant", "user"):
        return None
    stamp = parse_time(data.get("timestamp"))
    return None if stamp is None else (data, stamp)


def _entry(data: dict[str, Any], stamp: datetime) -> _Entry | None:
    """Usage, time, and tool calls of an assistant message line, else None."""
    if data.get("type") != "assistant":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    counts: dict[str, int] = {}
    for name in TOKEN_FIELDS:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            counts[name] = value
    if not counts:
        return None
    # One API message is written as several lines (one per content block) that repeat its usage.
    key: object = next(
        (k for k in (message.get("id"), data.get("requestId"), data.get("uuid")) if isinstance(k, str) and k),
        object(),
    )
    cost = data.get("costUSD")
    if not isinstance(cost, int | float) or isinstance(cost, bool) or cost < 0:
        cost = None
    return _Entry(key, stamp, counts, None if cost is None else float(cost), _tools(message.get("content")))


def _event(data: dict[str, Any]) -> tuple[str, str] | None:
    """What a line of the session's own transcript ends, for splitting time; None to skip it.

    "model" for an assistant message, "tools" or "approval" for a tool result (whether you
    answered a permission prompt for it) with its tool-use id, "human" for a prompt you typed,
    "prompt" for one the app sent (a task notification, a hand-back). Meta lines (skill text,
    hook context) are skipped.
    """
    if data.get("type") == "assistant" or data.get("isCompactSummary") is True:
        return ("model", "")  # a compaction summary ends time Claude spent summarizing
    if data.get("isMeta") is True:
        return None
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            tool_id = block.get("tool_use_id")
            decision = data.get("permissionDecision")
            source = decision.get("source") if isinstance(decision, dict) else None
            kind = "approval" if isinstance(source, str) and source.startswith("user") else "tools"
            return (kind, tool_id if isinstance(tool_id, str) else "")
    origin = data.get("origin")
    return ("human" if isinstance(origin, dict) and origin.get("kind") == "human" else "prompt", "")


_PART = {"model": "model", "tools": "tools", "approval": "approvals", "human": "waiting", "prompt": "waiting"}


@dataclass
class _Split:
    parts: dict[str, float]  # seconds per part of TIME_PARTS
    tools: dict[str, float]  # seconds of the tools and approvals parts, per tool name
    prompts: int
    approvals: int


def _split(
    events: list[tuple[datetime, int, str, str]], names: dict[str, str], start: datetime, last: datetime
) -> _Split:
    """Where the time from ``start`` to ``last`` went, from (time, order, event, tool-use id) lines.

    Each gap between two lines goes to what ended it: the model writing a message, a tool
    running (or waiting for your approval), or a prompt arriving after the turn had ended. Time
    after the session's last line goes to tools: a subagent was still working. Lines with the
    same time keep their order in the file. A prompt before Claude's first message in the window
    is the one that started the task, not a follow-up.
    """
    split = _Split(dict.fromkeys(TIME_PARTS, 0.0), {}, 0, 0)
    previous = start
    answered = False  # Claude has written a message in the window
    for stamp, _, event, tool_id in sorted(events):
        if stamp > last:
            break
        split.prompts += event == "human" and answered
        split.approvals += event == "approval"
        answered = answered or event == "model"
        if stamp > previous:
            gap = (stamp - previous).total_seconds()
            split.parts[_PART[event]] += gap
            name = names.get(tool_id) if tool_id else None
            if name is not None:
                split.tools[name] = split.tools.get(name, 0.0) + gap
            previous = stamp
    if last > previous:
        split.parts["tools"] += (last - previous).total_seconds()
    return split


def read_usage(files: list[Path], start: datetime, end: datetime) -> Usage | None:
    """Usage of the assistant messages written between ``start`` and ``end``, or None.

    None also when reading would take more than the time or byte budget.
    """
    deadline = time.monotonic() + _BUDGET_SECONDS
    budget = _BUDGET_BYTES
    totals = dict.fromkeys(TOKEN_FIELDS, 0)
    seen: set[object] = set()
    seen_tools: set[str] = set()
    tool_calls: dict[str, int] = {}
    messages = 0
    last: datetime | None = None
    cost: float | None = None
    events: list[tuple[datetime, int, str, str]] = []
    names: dict[str, str] = {}  # tool-use id -> tool name, in the session's own transcript
    seen_lines: set[str] = set()
    split_seconds = 0.0  # spent decoding user lines; past _SPLIT_SECONDS there is no split
    for path in files:
        # A file last written before the route (a finished subagent) has nothing in the window.
        if path.stat().st_mtime < (start - _SLACK).timestamp():
            continue
        own = path.parent.name != "subagents"  # the session's transcript, not a subagent's
        with closing(_lines_from_end(path)) as lines:
            for order, line in enumerate(lines):
                budget -= len(line) + 1
                if budget < 0 or time.monotonic() > deadline:
                    raise _OverBudget(path)
                if b'"usage"' in line and b'"assistant"' in line:
                    decoded = _decode(line)
                elif own and split_seconds <= _SPLIT_SECONDS and _USER_LINE.search(line):
                    began = time.monotonic()
                    decoded = _decode(line)
                    spent = time.monotonic() - began
                    split_seconds += spent
                    deadline += spent  # the split's own time never costs the token capture
                else:
                    continue
                if decoded is None:
                    continue
                data, stamp = decoded
                assistant = data.get("type") == "assistant"
                if stamp < start:
                    # Only assistant lines end the scan, as before the split existed.
                    if stamp < start - _SLACK and assistant:
                        break
                    continue
                if stamp > end:
                    continue
                uid = data.get("uuid")
                event = _event(data) if own and not (isinstance(uid, str) and uid in seen_lines) else None
                if event is not None:
                    if isinstance(uid, str):
                        seen_lines.add(uid)
                    # Read from the end: a later line in the file gets a smaller order.
                    events.append((stamp, -order, *event))
                if not assistant:
                    continue
                entry = _entry(data, stamp)
                if entry is None:
                    continue
                if own:
                    names.update(entry.tools)
                # Each content block of a message is its own line, so tool calls are counted per
                # line (once per tool-use id) and usage once per message.
                for tool_id, name in entry.tools:
                    if tool_id in seen_tools or (
                        name not in tool_calls and len(tool_calls) >= _MAX_TOOL_NAMES
                    ):
                        continue
                    seen_tools.add(tool_id)
                    tool_calls[name] = tool_calls.get(name, 0) + 1
                if entry.key in seen:
                    continue
                seen.add(entry.key)
                messages += 1
                for name, value in entry.counts.items():
                    totals[name] += value
                if entry.cost is not None:
                    cost = (cost or 0.0) + entry.cost
                last = entry.time if last is None else max(last, entry.time)
    if last is None:
        return None
    found = Usage(totals, messages, last, round(cost, 4) if cost is not None else None, tool_calls)
    if split_seconds <= _SPLIT_SECONDS and any(event[0] <= last for event in events):
        split = _split(events, names, start, last)
        found.time_split, found.tool_time = split.parts, split.tools
        found.user_prompts, found.approvals = split.prompts, split.approvals
    return found


def route_usage(
    route: dict[str, Any], routes: list[dict[str, Any]], end: datetime, home: Path | None = None
) -> tuple[Usage, datetime, datetime] | None:
    """Usage of ``route``'s session from its creation until ``end`` or the session's next route."""
    session = route.get("session_id")
    start = parse_time(route.get("created_at"))
    if not isinstance(session, str) or start is None:
        return None
    # Work after the session's next route belongs to that route. Records are appended in order.
    index = max((i for i, r in enumerate(routes) if r.get("id") == route.get("id")), default=len(routes))
    for later in routes[index + 1 :]:
        created = parse_time(later.get("created_at"))
        if later.get("session_id") == session and created is not None:
            end = min(end, created)
            break
    files = transcript_files(session, home)
    if not files or end <= start:
        return None
    try:
        usage = read_usage(files, start, end)
    except _OverBudget:
        return None
    return (usage, start, end) if usage is not None else None


def _minutes_by_tool(seconds: dict[str, float]) -> dict[str, float]:
    """Minutes per tool, most first, leaving out tools that round to zero."""
    minutes = {name: round(value / 60, 1) for name, value in seconds.items()}
    return dict(sorted(((n, m) for n, m in minutes.items() if m > 0), key=lambda item: (-item[1], item[0])))


def fill_outcome(
    outcome: dict[str, Any],
    route: dict[str, Any],
    store: RecordStore,
    config: Config,
    home: Path | None = None,
) -> None:
    """Fill the outcome's empty token, duration, cost, tool-call, and time fields from the transcripts.

    Only with ``[records] capture_usage = true``. Values given by hand are kept. ``tokens`` is
    the sum of ``token_counts`` (fresh input, cache writes, cache reads, output), which are kept
    apart so cache use stays visible; with ``tokens`` given by hand, ``token_counts`` is left out
    so the two never disagree. The filled fields are listed under ``captured``. Never raises.
    """
    if config.get("records", {}).get("capture_usage") is not True:
        return
    try:
        end = parse_time(outcome.get("recorded_at")) or datetime.now(UTC)
        found = route_usage(route, store.read("routes"), end, home)
        if found is None:
            return
        usage, start, end = found
        minutes = round(max((usage.last_at - start).total_seconds(), 0.0) / 60, 1)
        values: dict[str, Any] = {
            "tokens": usage.tokens,
            "token_counts": usage.counts,
            "duration_minutes": minutes,
            "cost_usd": usage.cost_usd,
            "tool_calls": dict(sorted(usage.tool_calls.items(), key=lambda item: (-item[1], item[0]))),
            "time_minutes": None
            if usage.time_split is None
            else {part: round(seconds / 60, 1) for part, seconds in usage.time_split.items()},
            "tool_minutes": None if usage.tool_time is None else _minutes_by_tool(usage.tool_time),
            "user_prompts": usage.user_prompts,
            "approvals": usage.approvals,
        }
        if outcome.get("tokens") is not None:
            values["token_counts"] = None
        filled = [k for k in CAPTURED_FIELDS if outcome.get(k) is None and values[k] is not None]
        if not filled:
            return
        outcome.update({k: values[k] for k in filled})
        outcome["captured"] = {
            "source": "claude-code-transcripts",
            "fields": filled,
            "messages": usage.messages,
            "window": [start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")],
        }
    except Exception:  # capture is best effort and must never block recording an outcome
        return
