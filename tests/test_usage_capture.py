"""Automatic capture of tokens and duration from Claude Code session transcripts."""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from susanoo.adapters.claude_code import usage
from susanoo.adapters.claude_code.usage import fill_outcome, read_usage, transcript_files
from susanoo.cli import main
from susanoo.config import load_config
from susanoo.enforcement.auto import close_superseded
from susanoo.enforcement.state import RouteState
from susanoo.evaluation import RecordStore, summarize
from susanoo.render import render_report

SESSION = "0b90bb7e-bcc7-4c11-a34c-ed85054cd0e8"
START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _stamp(minutes: float) -> str:
    return (START + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def assistant(minutes: float, msg_id: str, output: int = 10, **extra: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _stamp(minutes),
        "requestId": f"req_{msg_id}",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "never read"}],
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1000,
                "output_tokens": output,
            },
        },
        **extra,
    }


def write_lines(path: Path, lines: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines),
        encoding="utf-8",
    )
    return path


def transcript(home: Path, lines: list[Any], session: str = SESSION, project: str = "D--proj") -> Path:
    return write_lines(home / "projects" / project / f"{session}.jsonl", lines)


def route(
    record_id: str = "su-1", minutes: float = 0, session: str | None = SESSION, **extra: Any
) -> dict[str, Any]:
    created = (START + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return {"id": record_id, "created_at": created, "session_id": session, "profile": "fast", **extra}


def store_with(tmp_path: Path, *routes: dict[str, Any]) -> RecordStore:
    store = RecordStore(tmp_path / "project")
    for item in routes:
        store.append("routes", item)
    return store


def enabled() -> dict[str, Any]:
    config = load_config()
    config["records"]["capture_usage"] = True
    return config


def outcome(minutes: float, **values: Any) -> dict[str, Any]:
    return {"record_id": "su-1", "recorded_at": _stamp(minutes), "status": "completed", **values}


def test_usage_counts_each_message_once_within_the_window(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(
        home,
        [
            assistant(-30, "msg_old", output=999),  # before the route
            {"type": "user", "timestamp": _stamp(1), "toolUseResult": {"usage": {"input_tokens": 50_000}}},
            assistant(2, "msg_a"),
            assistant(2, "msg_a"),  # the same API message, next content block
            "{not json",
            "[1, 2]",
            assistant(5, "msg_b", output=20, costUSD=0.25),
            assistant(90, "msg_late", output=999),  # after the outcome
        ],
    )
    sub = write_lines(
        main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [assistant(3, "msg_sub", output=7)]
    )
    files = transcript_files(SESSION, home)
    assert files == [main_file, sub]

    found = read_usage(files, START, START + timedelta(minutes=60))
    assert found is not None
    assert found.messages == 3
    assert found.counts == {
        "input_tokens": 15,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 3000,
        "output_tokens": 37,
    }
    assert found.tokens == 3352
    assert found.last_at == START + timedelta(minutes=5)
    assert found.cost_usd == 0.25


def test_reading_from_the_end_handles_chunk_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage, "_CHUNK", 7)
    home = tmp_path / "claude"
    transcript(home, [assistant(i, f"msg_{i}") for i in range(1, 21)])
    found = read_usage(transcript_files(SESSION, home), START, START + timedelta(hours=1))
    assert found is not None and found.messages == 20 and found.counts["output_tokens"] == 200


def test_reading_edge_cases(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(
        home,
        [
            assistant(1, "msg_a"),
            assistant(-2, "msg_b"),  # a little before the route: skipped, reading goes on
            assistant(3, "msg_c"),
        ],
    )
    main_file.write_bytes(main_file.read_bytes().rstrip(b"\n"))  # last line still being written
    # The same message in a subagent's transcript counts once.
    write_lines(main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [assistant(3, "msg_c")])
    found = read_usage(transcript_files(SESSION, home), START, START + timedelta(hours=1))
    assert found is not None and found.messages == 2


def test_files_last_written_before_the_route_are_skipped(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(home, [assistant(1, "msg_a")])
    sub = write_lines(main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [assistant(2, "msg_s")])
    old = (START - timedelta(hours=1)).timestamp()
    os.utime(sub, (old, old))
    found = read_usage(transcript_files(SESSION, home), START, START + timedelta(hours=1))
    assert found is not None and found.messages == 1


def test_capture_gives_up_past_its_reading_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "claude"
    transcript(home, [assistant(i, f"msg_{i}") for i in range(1, 30)])
    monkeypatch.setattr(usage, "_BUDGET_BYTES", 2000)
    data = outcome(60)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data == outcome(60)


def test_transcripts_are_only_looked_up_by_a_plain_session_id(tmp_path: Path):
    home = tmp_path / "claude"
    transcript(home, [assistant(1, "msg_a")])
    for bad in ("../D--proj/" + SESSION, "*", "", "a/b"):
        assert transcript_files(bad, home) == []
    assert transcript_files("missing-session", home) == []


def test_capture_is_off_unless_configured(tmp_path: Path):
    home = tmp_path / "claude"
    transcript(home, [assistant(2, "msg_a")])
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), load_config(), home)
    assert data == outcome(10)


def test_capture_fills_empty_fields_and_keeps_given_ones(tmp_path: Path):
    home = tmp_path / "claude"
    transcript(home, [assistant(2, "msg_a"), assistant(6, "msg_b")])
    store = store_with(tmp_path, route())

    data = outcome(10, tokens=None, duration_minutes=None, cost_usd=None)
    fill_outcome(data, route(), store, enabled(), home)
    assert data["tokens"] == 2 * 1115 and data["duration_minutes"] == 6.0
    assert data["cost_usd"] is None  # current transcripts carry no cost; none is estimated
    assert data["captured"]["fields"] == [
        "tokens",
        "token_counts",
        "duration_minutes",
        "tool_calls",
        "time_minutes",
        "tool_minutes",
        "user_prompts",
        "approvals",
    ]
    assert data["captured"]["messages"] == 2
    assert data["time_minutes"] == {"model": 6.0, "tools": 0.0, "approvals": 0.0, "waiting": 0.0}
    assert (data["tool_minutes"], data["user_prompts"], data["approvals"]) == ({}, 0, 0)
    assert data["token_counts"] == {
        "input_tokens": 10,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 2000,
        "output_tokens": 20,
    }
    assert data["tool_calls"] == {}
    assert "never read" not in json.dumps(data)

    given = outcome(10, tokens=42, duration_minutes=3.0)
    fill_outcome(given, route(), store, enabled(), home)
    assert given["tokens"] == 42 and given["duration_minutes"] == 3.0
    # Counts that would not add up to the given total are left out.
    assert given["captured"]["fields"] == [
        "tool_calls",
        "time_minutes",
        "tool_minutes",
        "user_prompts",
        "approvals",
    ]
    assert "token_counts" not in given


def tool_line(minutes: float, msg_id: str, tool_id: str, name: str) -> dict[str, Any]:
    line = assistant(minutes, msg_id)
    line["message"]["content"] = [{"type": "tool_use", "id": tool_id, "name": name, "input": {"secret": "x"}}]
    return line


def test_tool_calls_are_counted_by_name_without_reading_inputs(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(
        home,
        [
            tool_line(-20, "msg_old", "toolu_0", "Bash"),
            tool_line(1, "msg_a", "toolu_1", "Read"),
            tool_line(1, "msg_a", "toolu_2", "mcp__codebase-memory-mcp__search_graph"),  # same message
            tool_line(1, "msg_a", "toolu_2", "mcp__codebase-memory-mcp__search_graph"),  # repeated line
            {"type": "user", "timestamp": _stamp(2), "message": {"content": [{"type": "tool_result"}]}},
            tool_line(3, "msg_b", "toolu_3", "Read"),
            tool_line(4, "msg_c", "toolu_4", "x" * 500),  # not a plausible tool name
            tool_line(5, "msg_d", "toolu_5", "Read | injected"),
        ],
    )
    write_lines(
        main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [tool_line(2, "msg_s", "toolu_9", "Grep")]
    )
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["tool_calls"] == {"Read": 2, "Grep": 1, "mcp__codebase-memory-mcp__search_graph": 1}
    assert data["captured"]["messages"] == 5  # msg_a, msg_b, msg_c, msg_d, msg_s
    assert "secret" not in json.dumps(data)


def result_line(minutes: float, tool_id: str, source: str | None = None) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": "never read"}
    line: dict[str, Any] = {
        "type": "user",
        "timestamp": _stamp(minutes),
        "uuid": f"result-{tool_id}-{minutes}",
        "message": {"role": "user", "content": [block]},
        "toolUseResult": {"stdout": "never read"},
    }
    if source is not None:
        line["permissionDecision"] = {"decision": "accept", "source": source, "reason": "never read"}
    return line


def prompt_line(minutes: float, kind: str | None = "human", **extra: Any) -> dict[str, Any]:
    origin = None if kind is None else {"kind": kind, "body": "never read"}
    return {
        "type": "user",
        "timestamp": _stamp(minutes),
        "origin": origin,
        "message": {"role": "user", "content": "never read"},
        **extra,
    }


def test_time_is_split_by_what_ended_each_gap(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(
        home,
        [
            prompt_line(-1),  # the prompt that started the task comes before the route
            tool_line(1, "msg_a", "toolu_1", "Bash"),  # model 0-1
            result_line(4, "toolu_1", "config"),  # tools 1-4: allowed by settings
            tool_line(5, "msg_b", "toolu_2", "Edit"),  # model 4-5
            result_line(7, "toolu_2", "user_temporary"),  # approvals 5-7: you answered a permission prompt
            assistant(8, "msg_c"),  # model 7-8: the turn ends
            prompt_line(9, kind=None, isMeta=True),  # skill text: skipped
            prompt_line(20),  # waiting 8-20, then your follow-up
            tool_line(21, "msg_d", "toolu_3", "Agent"),  # model 20-21
            result_line(26, "toolu_3"),  # tools 21-26: the subagent ran
            assistant(27, "msg_e"),  # model 26-27
            prompt_line(28, kind="task-notification"),  # after the last message: next work
            prompt_line(40),
        ],
    )
    write_lines(main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [assistant(24, "msg_s")])
    data = outcome(45)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["duration_minutes"] == 27.0
    assert data["time_minutes"] == {"model": 5.0, "tools": 8.0, "approvals": 2.0, "waiting": 12.0}
    assert sum(data["time_minutes"].values()) == data["duration_minutes"]
    assert data["tool_minutes"] == {"Agent": 5.0, "Bash": 3.0, "Edit": 2.0}
    assert (data["user_prompts"], data["approvals"]) == (1, 1)
    assert "never read" not in json.dumps(data)


def test_time_split_edge_cases(tmp_path: Path):
    home = tmp_path / "claude"
    lines = [
        prompt_line(0.5, uuid="u-start"),  # the prompt that started the task, just after the route
        tool_line(2, "msg_a", "toolu_1", "Bash"),  # model 0.5-2
        result_line(2, "toolu_1", "user_temporary"),  # same time: file order keeps the gap on model
        tool_line(3, "msg_b", "", "Read"),  # model 2-3; a tool-use id that is empty
        result_line(4, ""),  # tools 3-4, credited to no tool
        prompt_line(6, uuid="u-follow"),  # waiting 4-6, a follow-up
        {"type": "user", "timestamp": _stamp(8), "isCompactSummary": True, "message": {"content": "x"}},
        assistant(9, "msg_c"),  # model 6-9: compaction, then the answer
    ]
    transcript(home, lines)
    transcript(home, lines, project="D--moved")  # the same session copied to another project folder
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["time_minutes"] == {"model": 5.5, "tools": 1.0, "approvals": 0.0, "waiting": 2.5}
    assert data["tool_minutes"] == {}  # neither the model gaps nor the result reach "Read"
    assert (data["user_prompts"], data["approvals"]) == (1, 1)


def test_split_has_its_own_budget_and_never_costs_the_token_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "claude"
    transcript(
        home, [tool_line(1, "msg_a", "toolu_1", "Bash"), result_line(4, "toolu_1"), assistant(5, "msg_b")]
    )
    monkeypatch.setattr(usage, "_SPLIT_SECONDS", -1.0)  # over the split's budget from the start
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["tokens"] == 2 * 1115 and data["duration_minutes"] == 5.0
    assert {"time_minutes", "tool_minutes", "user_prompts", "approvals"}.isdisjoint(data)


def test_time_after_the_sessions_last_line_goes_to_tools(tmp_path: Path):
    home = tmp_path / "claude"
    main_file = transcript(home, [tool_line(3, "msg_a", "toolu_1", "Agent")])
    write_lines(main_file.parent / SESSION / "subagents" / "agent-x.jsonl", [assistant(9, "msg_s")])
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["time_minutes"] == {"model": 3.0, "tools": 6.0, "approvals": 0.0, "waiting": 0.0}
    assert data["tool_minutes"] == {}  # the subagent's tail has no tool result to name it

    # Without a line of the session's own transcript in the window there is no split.
    transcript(home, [assistant(-20, "msg_old")])
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data["duration_minutes"] == 9.0
    assert {"time_minutes", "tool_minutes", "user_prompts", "approvals"}.isdisjoint(data)


def test_work_after_the_sessions_next_route_belongs_to_that_route(tmp_path: Path):
    home = tmp_path / "claude"
    transcript(home, [assistant(2, "msg_a"), assistant(8, "msg_b"), assistant(12, "msg_c")])
    other_session = route("su-0", minutes=4, session="another-session")
    store = store_with(tmp_path, route(), other_session, route("su-2", minutes=10))
    data = outcome(30)
    fill_outcome(data, route(), store, enabled(), home)
    assert data["captured"]["messages"] == 2 and data["duration_minutes"] == 8.0
    assert data["captured"]["window"][1] == (START + timedelta(minutes=10)).isoformat(timespec="seconds")


@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["garbage"],
        [{**assistant(2, "msg_a"), "message": {"id": "msg_a", "usage": [1, 2]}}],
        [{**assistant(2, "msg_a"), "timestamp": "yesterday"}],
        [{**assistant(2, "msg_a"), "message": {"id": "msg_a", "usage": {"input_tokens": "5"}}}],
    ],
)
def test_unexpected_transcripts_capture_nothing(tmp_path: Path, lines: list[Any]):
    home = tmp_path / "claude"
    transcript(home, lines)
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data == outcome(10)


@pytest.mark.parametrize("record", [route(session=None), route(created_at="not a time"), route(minutes=20)])
def test_routes_without_a_session_or_window_capture_nothing(tmp_path: Path, record: dict[str, Any]):
    home = tmp_path / "claude"
    transcript(home, [assistant(2, "msg_a")])
    data = outcome(10)
    fill_outcome(data, record, store_with(tmp_path, record), enabled(), home)
    assert "captured" not in data


def test_unreadable_transcript_fails_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "claude"
    transcript(home, [assistant(2, "msg_a")])

    def broken(path: Path) -> Any:
        raise PermissionError(path)

    monkeypatch.setattr(usage, "_lines_from_end", broken)
    data = outcome(10)
    fill_outcome(data, route(), store_with(tmp_path, route()), enabled(), home)
    assert data == outcome(10)


def test_superseded_routes_are_captured_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    now = datetime.now(UTC)
    record = route()
    record["created_at"] = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    line = assistant(0, "msg_a")
    line["timestamp"] = (now - timedelta(minutes=4)).isoformat()
    transcript(home, [line])
    store = store_with(tmp_path, record)
    close_superseded(store, RouteState(route=record, approved=False, closed=False), "superseded", enabled())
    [closed] = store.read("outcomes")
    assert closed["source"] == "susanoo" and closed["tokens"] == 1115
    assert closed["duration_minutes"] == 6.0

    # A transcript that cannot be read still closes the route, without capture.
    monkeypatch.setattr(usage, "_lines_from_end", lambda path: (_ for _ in ()).throw(OSError(path)))
    close_superseded(store, RouteState(route=record, approved=False, closed=False), "again", enabled())
    assert store.read("outcomes")[-1]["notes"] == "again" and "tokens" not in store.read("outcomes")[-1]


def test_report_shows_tokens_and_duration_per_profile():
    routes = [route("su-1"), route("su-2", profile="deep"), route("su-3", profile="deep")]
    outcomes = [
        {"record_id": "su-1", "status": "completed", "tokens": 1200, "duration_minutes": 4.0, "captured": {}},
        {"record_id": "su-2", "status": "completed", "tokens": 2_000_000, "duration_minutes": 30.0},
        {"record_id": "su-3", "status": "completed", "tokens": 4_000_000},
    ]
    report = summarize(routes, outcomes, [])
    fast, deep = report.profiles
    assert (fast.mean_tokens, fast.with_tokens, fast.timed, fast.captured) == (1200, 1, 1, 1)
    assert (deep.mean_tokens, deep.with_tokens, deep.mean_minutes, deep.timed) == (3_000_000, 2, 30.0, 1)
    text = render_report(routes, report, {o["record_id"]: o for o in outcomes}, 10)
    assert "| FAST | 1 | 1 | 100% | 4 (n=1) | 1.2k (n=1) |" in text
    assert "| DEEP | 2 | 2 | 100% | 30 (n=1) | 3.0M (n=2) |" in text
    assert "1 outcome(s) took them from Claude Code session transcripts" in text
    assert "not a comparison of workflows" in text
    assert "validated" not in text
    assert "Token usage by profile" not in text  # no outcome has separate counts


def test_report_shows_cache_use_and_tools_per_profile():
    routes = [route("su-1"), route("su-2"), route("su-3", profile="deep")]
    counts = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 1600,
        "output_tokens": 50,
    }
    outcomes = [
        {
            "record_id": "su-1",
            "status": "completed",
            "token_counts": counts,
            "tool_calls": {"Read": 3, "Grep": 1},
        },
        {"record_id": "su-2", "status": "completed", "token_counts": {**counts, "input_tokens": 2100}},
        {
            "record_id": "su-3",
            "status": "completed",
            "token_counts": {"input_tokens": "lots"},
            "tool_calls": [1],
        },
    ]
    report = summarize(routes, outcomes, [])
    fast, deep = report.profiles
    assert fast.with_token_counts == 2 and fast.token_count_totals["input_tokens"] == 2200
    assert fast.cache_hit_ratio == 3200 / (3200 + 600 + 2200)
    assert fast.to_dict()["cache_hit_ratio"] == fast.cache_hit_ratio
    assert deep.with_token_counts == 0 and deep.cache_hit_ratio is None and deep.tool_calls == {}
    text = render_report(routes, report, {o["record_id"]: o for o in outcomes}, 10)
    assert "## Token usage by profile" in text
    assert "| FAST | 2 | 1.1k | 300 | 1.6k | 50 | 53% |" in text
    assert "| DEEP |" not in text.split("## Token usage by profile")[1]
    assert "- FAST: Read 3, Grep 1" in text
    data = report.to_dict()["profiles"][0]
    assert data["with_token_counts"] == 2 and data["tool_calls"] == {"Read": 3, "Grep": 1}
    assert data["token_count_totals"]["cache_read_input_tokens"] == 3200


def test_report_shows_where_the_time_went():
    routes = [route("su-1"), route("su-2"), route("su-3"), route("su-4", profile="deep")]
    split = {"model": 4.0, "tools": 10.0, "approvals": 1.0, "waiting": 5.0}
    outcomes = [
        {
            "record_id": "su-1",
            "status": "completed",
            "time_minutes": split,
            "tool_minutes": {"Bash": 9.0, "Agent": 1.0},
            "user_prompts": 3,
            "approvals": 2,
        },
        {
            "record_id": "su-2",
            "status": "completed",
            "time_minutes": {**split, "tools": 0},
            "tool_minutes": {"Edit": 1.0, "Bash": "slow", "Read": float("inf"), "Grep": -2},
            "user_prompts": 1,
        },
        {"record_id": "su-3", "status": "completed", "time_minutes": {**split, "waiting": float("nan")}},
        {"record_id": "su-4", "status": "completed", "time_minutes": "lots", "user_prompts": 9},
    ]
    report = summarize(routes, outcomes, [])
    fast, deep = report.profiles
    assert fast.with_time == 2
    assert fast.time_totals == {"model": 8.0, "tools": 10.0, "approvals": 2.0, "waiting": 10.0}
    assert (fast.user_prompts, fast.approvals) == (4, 2)
    assert deep.with_time == 0 and deep.user_prompts == 0
    text = render_report(routes, report, {o["record_id"]: o for o in outcomes}, 10)
    assert "## Where the time went" in text
    assert "| FAST | 2 | 4.0 (27%) | 5.0 (33%) | 1.0 (7%) | 5.0 (33%) | 2.0 | 1.0 |" in text
    assert "| DEEP |" not in text.split("## Where the time went")[1]
    assert "- FAST: Bash 9, Agent 1, Edit 1" in text
    assert report.to_dict()["profiles"][0]["time_totals"]["tools"] == 10.0


def test_report_cost_line_stays_out_of_the_table():
    routes = [route("su-1")]
    report = summarize(routes, [{"record_id": "su-1", "status": "completed", "cost_usd": 1.5}], [])
    text = render_report(routes, report, {}, 10)
    assert "|\n\nTotal cost where recorded: FAST $1.5." in text


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([*args, "--path", str(root)], stdin=io.StringIO(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_outcome_command_captures_usage_when_enabled(
    python_project: Path, monkeypatch: pytest.MonkeyPatch, home
):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    claude = Path(usage.claude_home())
    assert run(python_project, "init", "--no-auto")[0] == 0
    _, out, _ = run(python_project, "route", "--json", "--session", SESSION, "Fix a typo in the README")
    record_id = json.loads(out)["record_id"]

    # Pretend the route was made ten minutes ago, with Claude working on it since.
    routes = python_project / ".susanoo/records/routes.jsonl"
    record = json.loads(routes.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    record["created_at"] = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    routes.write_text(json.dumps(record) + "\n", encoding="utf-8")
    line = assistant(0, "msg_a")
    line["timestamp"] = (now - timedelta(minutes=3)).isoformat()
    transcript(claude, [line])

    _, out, _ = run(python_project, "outcome", record_id, "--status", "completed", "--json")
    assert json.loads(out)["tokens"] is None  # off by default

    with (python_project / ".susanoo/config.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n[records]\ncapture_usage = true\n")
    _, out, _ = run(python_project, "outcome", record_id, "--status", "completed")
    assert "captured from the session transcript: tokens, token_counts, duration_minutes, tool_calls, " in out
    _, out, _ = run(python_project, "report")
    assert "7 (n=1) | 1.1k (n=1)" in out
