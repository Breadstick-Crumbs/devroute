"""Land records: one JSON line per run, shared by every worktree of a repository.

They live in ``<git common dir>/susanoo/records/land.jsonl`` rather than in a worktree's
``.susanoo/records``, because agent worktrees are deleted after their session. They are local,
never committed, and never pushed. On Windows an antivirus scanner or another process can hold
the file for a moment, so writes retry.
"""

from __future__ import annotations

import json
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


def land_records_path(common: Path) -> Path:
    return common / "susanoo" / "records" / LAND_FILE


def append_record(common: Path, record: dict[str, Any]) -> Path:
    path = land_records_path(common)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    for attempt in range(WRITE_ATTEMPTS):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # One write call per line, so concurrent appends do not interleave within a line.
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            return path
        except PermissionError:
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(0.1 * 2**attempt)
    return path  # pragma: no cover


def read_records(common: Path) -> list[dict[str, Any]]:
    path = land_records_path(common)
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
    """The newest run whose checks passed on exactly this combined tree, base, and check set."""
    for record in reversed(records):
        if (
            record.get("checks_passed") is True
            and record.get("combined_tree") == combined_tree
            and record.get("base_tree") == base_tree
            and record.get("checks_key") == key
        ):
            return dict(record)
    return None


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts for ADR 0014's go or no-go rule. Counts, not validated thresholds."""
    runs = [r for r in records if r.get("kind") == "land"]
    multi = [r for r in runs if len(r.get("candidates", [])) >= 2]
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
