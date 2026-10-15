"""Land records: one JSON line per run, shared by every worktree of a repository.

They live in ``<git common dir>/susanoo/records/land.jsonl`` (review records, ADR 0015, next to
them in ``reviews.jsonl``) rather than in a worktree's
``.susanoo/records``, because agent worktrees are deleted after their session. They are local,
never committed, and never pushed. On Windows an antivirus scanner or another process can hold
the file for a moment, so writes retry.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

LAND_FILE = "land.jsonl"
WRITE_ATTEMPTS = 6

# Fixed by ADR 0014 before any data existed; stored in every record so a later change cannot
# reinterpret old runs.
DECISION_RULE = "adr-0014-v1"
GO_MIN_RUNS = 20
GO_MIN_INTERACTIONS = 1
GO_MIN_AUTO_RESOLVED = 5
GO_MAX_MEDIAN_OVERHEAD_S = 60.0


def land_records_path(common: Path, name: str = LAND_FILE) -> Path:
    return common / "susanoo" / "records" / name


def append_record(common: Path, record: dict[str, Any], name: str = LAND_FILE) -> Path:
    path = land_records_path(common, name)
    line = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    for attempt in range(WRITE_ATTEMPTS):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # One unbuffered binary write per line in append mode, so concurrent runs do not
            # interleave within a line.
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            return path
        except PermissionError:
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.1 * 2**attempt)
    return path  # pragma: no cover


def read_records(common: Path, name: str = LAND_FILE) -> list[dict[str, Any]]:
    path = land_records_path(common, name)
    for attempt in range(WRITE_ATTEMPTS):
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            break
        except PermissionError:
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.1 * 2**attempt)
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            items.append(value)
    return items


def find_pass(
    records: list[dict[str, Any]], combined_tree: str, base_tree: str, key: str
) -> dict[str, Any] | None:
    """The newest run whose checks passed on exactly this tree, base, and check set."""
    for record in reversed(records):
        if (
            record.get("checks_key") == key
            and record.get("base_tree") == base_tree
            and combined_tree in (record.get("passed_trees") or [])
        ):
            return dict(record)
    return None


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts for ADR 0014's go or no-go rule. Counts, not validated thresholds."""
    runs = [r for r in records if r.get("kind") == "land"]
    # A landing run as "ready" and then with --merge is one landing: the last run per combined
    # tree counts once.
    last: dict[str, dict[str, Any]] = {}
    for r in runs:
        last[str(r.get("combined_tree") or r.get("id"))] = r
    multi = [r for r in last.values() if len(r.get("candidates", [])) >= 2]
    culprits: dict[str, int] = {}
    for r in runs:
        kind = (r.get("culprit") or {}).get("kind")
        if kind:
            culprits[kind] = culprits.get(kind, 0) + 1
    auto_resolved = sum(1 for r in multi if any(c.get("resolved") for c in r.get("conflicts", [])))
    unresolved = sum(1 for r in runs if r.get("result") == "conflict")
    interactions = sum(1 for r in multi if (r.get("culprit") or {}).get("kind") == "interaction")
    overheads = [
        float(r["duration_s"]) - float(r.get("check_seconds") or 0.0)
        for r in runs
        if isinstance(r.get("duration_s"), int | float) and not r.get("checks_reused")
    ]
    median_overhead = round(statistics.median(overheads), 1) if overheads else None
    results: dict[str, int] = {}
    for r in runs:
        results[str(r.get("result"))] = results.get(str(r.get("result")), 0) + 1
    if len(multi) < GO_MIN_RUNS:
        verdict = f"undecided: {len(multi)} of {GO_MIN_RUNS} runs with two or more candidates"
    elif (
        (interactions >= GO_MIN_INTERACTIONS or auto_resolved >= GO_MIN_AUTO_RESOLVED)
        and median_overhead is not None
        and median_overhead < GO_MAX_MEDIAN_OVERHEAD_S
    ):
        verdict = "go"
    else:
        verdict = "no-go"
    return {
        "rule": DECISION_RULE,
        "runs": len(runs),
        "multi_candidate_runs": len(multi),
        "results": results,
        "auto_resolved_runs": auto_resolved,
        "unresolved_conflict_runs": unresolved,
        "culprits": culprits,
        "interactions": interactions,
        "flaky_runs": sum(1 for r in runs if r.get("flaky")),
        "median_overhead_s": median_overhead,
        "verdict": verdict,
    }
