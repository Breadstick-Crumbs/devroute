"""Token usage and activity time of a routed task, read from Claude Code session transcripts.

Opt-in with ``[records] capture_usage = true``. Claude Code keeps one transcript per session at
``<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<project>/<session>.jsonl``, with subagent
transcripts in ``<session>/subagents/``. Each assistant message line carries the API usage
counts and a timestamp.

Only lines that look like assistant messages with usage are decoded, and from those only the
``usage`` counts, the ``timestamp``, the message and request ids (to count a message once),
``costUSD`` (present in older transcript formats), and the ``type``, ``id``, and ``name`` of
tool-use blocks (to count tool calls by tool) are read. Message text and tool inputs and
results are never read or stored. The transcript format is not a documented interface, so
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
from susanoo.evaluation.report import TOKEN_FIELDS
from susanoo.evaluation.store import RecordStore

CAPTURED_FIELDS = ("tokens", "token_counts", "duration_minutes", "cost_usd", "tool_calls")
_TOOL_BLOCKS = ("tool_use", "server_tool_use")
_MAX_TOOL_NAMES = 200
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CHUNK = 1 << 20
# Transcripts are read from the end and reading stops this far before the route was created,
# so a long session costs only the part of it the route covers.
_SLACK = timedelta(minutes=5)
# Capture also runs in the prompt hook: past this much reading, nothing is captured.
_BUDGET_BYTES = 64 << 20
_BUDGET_SECONDS = 2.0


class _OverBudget(Exception):
    pass


@dataclass
class Usage:
    counts: dict[str, int]
    messages: int
    last_at: datetime
    cost_usd: float | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)

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


def _entry(line: bytes) -> _Entry | None:
    """Usage, time, and tool calls of an assistant message line, else None."""
    if b'"usage"' not in line or b'"assistant"' not in line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "assistant":
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    stamp = parse_time(data.get("timestamp"))
    if not isinstance(usage, dict) or stamp is None:
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
    for path in files:
        # A file last written before the route (a finished subagent) has nothing in the window.
        if path.stat().st_mtime < (start - _SLACK).timestamp():
            continue
        with closing(_lines_from_end(path)) as lines:
            for line in lines:
                budget -= len(line) + 1
                if budget < 0 or time.monotonic() > deadline:
                    raise _OverBudget(path)
                entry = _entry(line)
                if entry is None:
                    continue
                if entry.time < start:
                    if entry.time < start - _SLACK:
                        break
                    continue
                if entry.time > end:
                    continue
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
    return Usage(totals, messages, last, round(cost, 4) if cost is not None else None, tool_calls)


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


def fill_outcome(
    outcome: dict[str, Any],
    route: dict[str, Any],
    store: RecordStore,
    config: Config,
    home: Path | None = None,
) -> None:
    """Fill the outcome's empty token, duration, cost, and tool-call fields from the transcripts.

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
