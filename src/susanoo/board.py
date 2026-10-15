"""`susanoo board` (ADR 0015): one line per worktree branch, for the lead.

Per branch it shows what the app and plain git listing do not: whether the branch's checks and
review hold **for its current commit**, its newest `susanoo land` result, and what it shares with
other branches (files both change, files ``[[land.keep_both]]`` may resolve, decision-record
numbers taken twice).

Strictly read-only and local. Git runs with ``--no-optional-locks -c core.fsmonitor=false`` and
without the variables that point it at another repository or add configuration
(``verification.commit``); nothing is fetched, and no file, index, or record is written. Text
taken from records is cut to one line and shown as data.
"""

from __future__ import annotations

import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from susanoo.architecture import load_architecture_spec
from susanoo.config import ConfigError, load_config
from susanoo.enforcement.state import evidence_status, is_verified, unverified_steps
from susanoo.evaluation.store import RecordStore
from susanoo.land.keepboth import KeepBothRule, parse_rules
from susanoo.land.records import read_records
from susanoo.land.reviews import REVIEW_TOOLS, read_reviews, review_for
from susanoo.land.runner import DEFAULT_BASE, SCRATCH_PREFIX
from susanoo.paths import is_initialized
from susanoo.verification.commit import _git as read_git

REMOTE = "origin"
TEXT_CHARS = 60  # longest text shown from a record or for a path
PATHS_SHOWN = 3  # shared paths named per other branch before "+n more"
SHORT_SHA = 7
WORKERS = 8  # branches read at once

_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_RECORD_NUMBER = re.compile(r"^(\d{3,5})(?=[-_. ])")


class BoardError(Exception):
    """A user-facing failure of `susanoo board`."""


@dataclass
class Worktree:
    """One entry of ``git worktree list --porcelain``."""

    path: Path
    head: str | None = None
    branch: str | None = None
    bare: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False


def one_line(value: object, limit: int = TEXT_CHARS) -> str:
    """``value`` as one line of at most ``limit`` characters, control characters blanked."""
    text = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in str(value))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _canonical(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, ValueError):
        return os.path.normcase(str(path.absolute()))


@lru_cache(maxsize=1024)
def _canonical_text(text: str) -> str:
    """``_canonical`` of a path from a record: many evidence records name the same checkout."""
    return _canonical(Path(text))


def list_worktrees(root: Path) -> list[Worktree] | None:
    """The repository's worktrees, main one first; None when git cannot list them."""
    text, sep = read_git(root, "worktree", "list", "--porcelain", "-z"), "\0"
    if text is None:  # git before 2.36 has no -z here
        text, sep = read_git(root, "worktree", "list", "--porcelain"), "\n"
    if text is None:
        return None
    found: list[Worktree] = []
    current: Worktree | None = None
    for item in [*text.split(sep), ""]:
        if item.startswith("worktree "):
            if current is not None:
                found.append(current)
            current = Worktree(Path(item[len("worktree ") :]))
        elif current is None:
            continue
        elif item.startswith("HEAD "):
            current.head = item[len("HEAD ") :].strip().lower()
        elif item.startswith("branch refs/heads/"):
            current.branch = item[len("branch refs/heads/") :]
        elif item == "bare":
            current.bare = True
        elif item == "detached":
            current.detached = True
        elif item == "locked" or item.startswith("locked "):
            current.locked = True
        elif item == "prunable" or item.startswith("prunable "):
            current.prunable = True
        elif not item:
            found.append(current)
            current = None
    return found


def _skip_reason(wt: Worktree, base: str) -> str | None:
    if wt.path.name == "tree" and wt.path.parent.name.startswith(SCRATCH_PREFIX):
        return "land scratch worktree"
    if wt.prunable or not wt.path.is_dir():
        return "prunable: its folder is gone"
    if wt.detached or not wt.branch:
        return "detached HEAD"
    if not wt.head or not _SHA.fullmatch(wt.head) or not wt.head.strip("0"):
        return "no commit"  # an unborn (orphan) branch is listed with an all-zero HEAD
    if wt.branch == base:
        return "the base branch"
    return None


def _default_base(root: Path) -> str:
    """The remote's default branch when git knows it, else ``main``."""
    ref = read_git(root, "symbolic-ref", "--quiet", f"refs/remotes/{REMOTE}/HEAD")
    prefix = f"refs/remotes/{REMOTE}/"
    if ref and ref.strip().startswith(prefix):
        return ref.strip()[len(prefix) :]
    return DEFAULT_BASE


def _base_ref(root: Path, base: str) -> str | None:
    """``origin/<base>`` as last fetched, or the local ``<base>`` when only it exists or it is
    ahead (commits not pushed or fetched yet would otherwise show as every branch's changes)."""
    remote, local = f"refs/remotes/{REMOTE}/{base}", f"refs/heads/{base}"
    have = [
        ref
        for ref in (remote, local)
        if read_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    ]
    if len(have) == 2 and _is_ancestor(root, remote, local) and not _is_ancestor(root, local, remote):
        return local
    return have[0] if have else None


def _nul_list(raw: str | None) -> list[str]:
    return [p for p in raw.split("\0") if p] if raw else []


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return read_git(root, "merge-base", "--is-ancestor", ancestor, descendant) is not None


def _ahead(root: Path, older: str, tip: str) -> int | None:
    """Commits on ``tip`` since ``older``, when ``older`` is an ancestor of it."""
    if not _SHA.fullmatch(older) or not _is_ancestor(root, older, tip):
        return None
    count = read_git(root, "rev-list", "--count", f"{older}..{tip}")
    return int(count.strip()) if count and count.strip().isdigit() else None


def _uncommitted(path: Path) -> tuple[list[str], list[str]] | None:
    """(every uncommitted path, both sides of renames; the new ones) in the worktree at ``path``."""
    raw = read_git(
        path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"
    )
    if raw is None:
        return None
    entries = raw.split("\0")
    paths: list[str] = []
    added: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, name = entry[:2], entry[3:]
        paths.append(name)
        if xy == "??" or "A" in xy:
            added.append(name)
        if ("R" in xy or "C" in xy) and i < len(entries) and entries[i]:
            paths.append(entries[i])  # the rename's source
            i += 1
    return paths, added


# --- checks -------------------------------------------------------------------------------


def _routes_by_checkout(
    listed: list[Worktree],
) -> dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """(routes, evidence) of every worktree that has records, keyed by its canonical path."""
    found: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for wt in listed:
        if wt.bare or not wt.path.is_dir() or not is_initialized(wt.path):
            continue
        store = RecordStore(wt.path)
        routes = store.read("routes")
        if routes:
            found[_canonical(wt.path)] = (routes, store.read("evidence"))
    return found


def _route_for(
    path: Path, stores: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The worktree's newest route and the evidence next to it.

    A worktree without routes of its own may hold the work for a route recorded in another
    checkout (ADR 0017); then the newest such route with evidence run here counts.
    """
    key = _canonical(path)
    if key in stores:
        routes, evidence = stores[key]
        return routes[-1], evidence
    best: tuple[str, dict[str, Any], list[dict[str, Any]]] | None = None
    for other, (routes, evidence) in stores.items():
        if other == key:
            continue
        ran_here = {
            item.get("record_id")
            for item in evidence
            if item.get("checkout") and _canonical_text(str(item.get("checkout"))) == key
        }
        for route in routes:
            stamp = str(route.get("created_at", ""))
            if route.get("id") in ran_here and (best is None or stamp >= best[0]):
                best = (stamp, route, evidence)
    return (best[1], best[2]) if best else (None, [])


def _newest_counted(route: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per step, the newest evidence the gate counts (as ``enforcement.state.unverified_steps``)."""
    ready = {str(s.get("id")) for s in route.get("verification", []) if s.get("mode") == "ready"}
    newest: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if item.get("record_id") != route.get("id"):
            continue
        step = str(item.get("step"))
        executed = item.get("source") == "executed" and item.get("status") != "skipped"
        if executed or (step not in ready and item.get("source") == "reported"):
            newest[step] = item
    return newest


def _checks(
    root: Path, route: dict[str, Any] | None, evidence: list[dict[str, Any]], tip: str
) -> dict[str, Any]:
    state: dict[str, Any] = {"state": "unverified", "route": None, "steps": [], "head": None, "commits": None}
    if route is None:
        return state
    state["route"] = one_line(route.get("id"))
    if is_verified(route, evidence, tip):
        return {**state, "state": "verified", "head": tip}
    missing, failing = evidence_status(route, evidence)
    if failing:
        return {**state, "state": "failed", "steps": [one_line(s) for s in failing]}
    steps = unverified_steps(route, evidence, tip)
    state["steps"] = [one_line(s) for s in steps]
    if missing or not steps:
        return state
    # Stale: every step passed, on clean trees of commits this branch has since built on.
    newest = _newest_counted(route, evidence)
    heads: set[str] = set()
    for step in steps:
        item = newest.get(step) or {}
        head = item.get("head")
        if not isinstance(head, str) or item.get("dirty") is not False:
            return state
        heads.add(head.lower())
    behind = {head: _ahead(root, head, tip) for head in heads}
    if not all(count for count in behind.values()):
        return state
    oldest = max(behind, key=lambda head: behind[head] or 0)
    return {**state, "state": "stale", "head": oldest, "commits": behind[oldest]}


def _land_result(record: dict[str, Any], cand: dict[str, Any]) -> str:
    """The run's result for this candidate: merged when it was, and who broke a failed run."""
    if cand.get("merged"):
        return "merged"
    result = one_line(record.get("result") or "?")
    culprit = record.get("culprit")
    blamed = culprit.get("candidate") if isinstance(culprit, dict) else None
    if result == "failed" and blamed and blamed != cand.get("label"):
        return f"failed (by {one_line(blamed, 30)})"
    return result


def _land(
    root: Path, lands: list[dict[str, Any]], branch: str, tip: str, own_commits: bool
) -> dict[str, Any] | None:
    """The newest `land` run of this branch at its tip, or one that pushed a commit built on it.

    The second needs commits of the branch's own: a fresh branch at an older base commit is an
    ancestor of every commit a run pushed for an earlier branch of the same name.
    """
    for record in reversed(lands):
        candidates = record.get("candidates")
        for cand in candidates if isinstance(candidates, list) else []:
            if not isinstance(cand, dict) or cand.get("branch") != branch:
                continue
            pushed = str(cand.get("pushed") or "").lower()
            if tip in (str(cand.get("tip") or "").lower(), pushed) or (
                own_commits and _SHA.fullmatch(pushed) and _is_ancestor(root, tip, pushed)
            ):
                return {
                    "result": _land_result(record, cand),
                    "id": one_line(record.get("id")),
                    "started_at": one_line(record.get("started_at")),
                }
    return None


# --- marks --------------------------------------------------------------------------------


def _decision_record(
    path: str, folders: tuple[str, ...], not_records: tuple[str, ...]
) -> tuple[str, str] | None:
    """(folder, number) when ``path`` is a numbered record directly in a decision-record folder."""
    folder, _, name = path.rpartition("/")
    if not folder or (folder + "/").lower() not in {f.lower() for f in folders}:
        return None
    if name.rsplit(".", 1)[0].lower() in not_records:
        return None
    match = _RECORD_NUMBER.match(name)
    return (folder, match.group(1)) if match else None


def _mark_shared(rows: list[dict[str, Any]], rules: list[KeepBothRule]) -> None:
    for row in rows:
        mine = set(row.get("changed") or [])
        for other in rows:
            if other is row or not mine:
                continue
            shared = sorted(mine & set(other.get("changed") or []))
            keep = [p for p in shared if any(rule.matches(p) for rule in rules)]
            plain = [p for p in shared if p not in keep]
            if plain:
                row["overlaps"].append({"branch": other["branch"], "paths": plain})
            if keep:
                row["append_only"].append({"branch": other["branch"], "paths": keep})


def _mark_records(root: Path, rows: list[dict[str, Any]], base: str, base_ref: str | None) -> None:
    """Flag decision-record numbers that a branch adds and another branch or the base also has."""
    spec = load_architecture_spec()
    taken: dict[tuple[str, str], list[tuple[str, str]]] = {}  # (folder, number) -> [(branch, file)]
    for row in rows:
        for path in row.pop("_added", []):
            found = _decision_record(path, spec.decisions, spec.not_records)
            if found:
                taken.setdefault((found[0].lower(), found[1]), []).append((row["branch"], path))
    on_base: dict[str, list[str]] = {}
    for (folder, number), holders in sorted(taken.items()):
        if base_ref is not None and folder not in on_base:
            listing = read_git(
                root, "ls-tree", "-z", "--name-only", f"{base_ref}:{holders[0][1].rpartition('/')[0]}"
            )
            on_base[folder] = _nul_list(listing)
        base_files = [
            name
            for name in on_base.get(folder, [])
            if (m := _RECORD_NUMBER.match(name)) and m.group(1) == number
        ]
        for branch, path in holders:
            name = path.rpartition("/")[2]
            # The same file in two branches is one branch built on the other, not a second claim.
            also = sorted({b for b, other in holders if b != branch and other != path})
            if any(f != name for f in base_files):
                also.append(base)
            if also:
                row = next(r for r in rows if r["branch"] == branch)
                row["records"].append({"number": number, "file": path, "also": also})


# --- the board ----------------------------------------------------------------------------


def _row(
    root: Path,
    wt: Worktree,
    base_ref: str | None,
    stores: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    reviews: list[dict[str, Any]],
    lands: list[dict[str, Any]],
) -> dict[str, Any]:
    tip = str(wt.head)
    branch = str(wt.branch)
    route, evidence = _route_for(wt.path, stores)
    found = {tool: review_for(reviews, tip, tool) for tool in REVIEW_TOOLS}
    changed: list[str] | None = None
    added: list[str] = []
    uncommitted = _uncommitted(wt.path)
    merge_base = read_git(root, "merge-base", base_ref, tip) if base_ref else None
    fork = merge_base.strip() if merge_base else ""
    if fork and uncommitted is not None:
        fields = _nul_list(read_git(root, "diff", "--name-status", "--no-renames", "-z", fork, tip))
        pairs = list(zip(fields[::2], fields[1::2], strict=False))  # (status letter, path)
        committed = [path for _, path in pairs]
        new = [path for status, path in pairs if status == "A"]
        changed = sorted({*committed, *uncommitted[0]})
        added = sorted({*new, *uncommitted[1]})
    return {
        "branch": branch,
        "path": str(wt.path),
        "tip": tip,
        "locked": wt.locked,
        "checks": _checks(root, route, evidence, tip),
        "review": {tool: one_line(r.get("status")) for tool, r in found.items() if r},
        "land": _land(root, lands, branch, tip, own_commits=fork != tip),
        "security": any("security" in str(c) for c in (route or {}).get("controls") or []),
        "uncommitted": len(uncommitted[0]) if uncommitted is not None else None,
        "changed": changed,
        "overlaps": [],
        "append_only": [],
        "records": [],
        "_added": added,
    }


def _safe_row(
    root: Path,
    wt: Worktree,
    base_ref: str | None,
    stores: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    reviews: list[dict[str, Any]],
    lands: list[dict[str, Any]],
) -> dict[str, Any]:
    """``_row``, or an unverified row naming the problem when a hand-edited record is malformed."""
    try:
        return _row(root, wt, base_ref, stores, reviews, lands)
    except (TypeError, AttributeError, ValueError, KeyError) as exc:
        return {
            "branch": str(wt.branch),
            "path": str(wt.path),
            "tip": str(wt.head),
            "locked": wt.locked,
            "checks": {"state": "unverified", "route": None, "steps": [], "head": None, "commits": None},
            "review": {},
            "land": None,
            "security": False,
            "uncommitted": None,
            "changed": None,
            "overlaps": [],
            "append_only": [],
            "records": [],
            "error": one_line(f"{type(exc).__name__}: {exc}", 120),
            "_added": [],
        }


def build_board(root: Path, base: str | None = None, config_file: Path | None = None) -> dict[str, Any]:
    """Everything `susanoo board` shows, as data. Reads only."""
    common = read_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    listed = list_worktrees(root) if common and common.strip() else None
    if not common or not common.strip() or listed is None:
        raise BoardError(f"{root} is not inside a git repository")
    base_name = base if base is not None else _default_base(root)
    if base_name.startswith("-") or read_git(root, "check-ref-format", "--branch", base_name) is None:
        raise BoardError(f"{one_line(base_name)!r} is not a branch name")
    warnings: list[str] = []
    base_ref = _base_ref(root, base_name)
    if base_ref is None:
        warnings.append(
            f"no {REMOTE}/{base_name} or local {base_name}: changed files and overlaps are not shown"
        )
    elif base_ref.startswith("refs/heads/"):
        warnings.append(f"compared with the local {base_name}: {REMOTE}/{base_name} is missing or behind it")
    try:
        rules = parse_rules(load_config(root, config_file).get("land", {}).get("keep_both"))
    except ConfigError as exc:
        rules = []
        warnings.append(
            f"[[land.keep_both]] not read ({one_line(exc, 120)}); every shared path shows as overlap"
        )
    repo = Path(common.strip())
    reviews = read_reviews(repo)
    lands = [r for r in read_records(repo) if r.get("kind") == "land"]
    stores = _routes_by_checkout(listed)
    kept: list[Worktree] = []
    skipped: list[dict[str, str]] = []
    for wt in listed:
        if wt.bare:
            continue
        reason = _skip_reason(wt, base_name)
        if reason is not None:
            skipped.append({"path": str(wt.path), "branch": wt.branch or "", "reason": reason})
        else:
            kept.append(wt)
    kept.sort(key=lambda wt: str(wt.branch))
    # Each git call costs a process start; branches are read side by side, in listing order.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(lambda wt: _safe_row(root, wt, base_ref, stores, reviews, lands), kept))
    _mark_shared(rows, rules)
    _mark_records(root, rows, base_name, base_ref)
    return {"base": base_name, "base_ref": base_ref, "rows": rows, "skipped": skipped, "warnings": warnings}


# --- text ---------------------------------------------------------------------------------


def _paths(paths: list[str]) -> str:
    shown = ", ".join(one_line(p) for p in paths[:PATHS_SHOWN])
    return shown + (f" +{len(paths) - PATHS_SHOWN} more" if len(paths) > PATHS_SHOWN else "")


def _checks_text(checks: dict[str, Any]) -> str:
    if checks["state"] == "verified":
        return f"verified@{str(checks['head'])[:SHORT_SHA]}"
    if checks["state"] == "stale":
        n = checks["commits"]
        return f"stale ({n} commit{'s' * (n != 1)})"
    return str(checks["state"])


def _review_text(review: dict[str, str]) -> str:
    parts = [review.get("code-review", "-")]
    parts += [
        f"{tool.removesuffix('-review')}:{status}" for tool, status in review.items() if tool != "code-review"
    ]
    return "review:" + ",".join(parts)


def _marks_text(row: dict[str, Any]) -> str:
    marks: list[str] = []
    if row.get("error"):
        marks.append(f"records unreadable: {row['error']}")
    if row["locked"]:
        marks.append("locked")
    if row["uncommitted"]:
        marks.append(f"uncommitted ({row['uncommitted']})")
    if row["security"]:
        marks.append("security")
    for item in row["overlaps"]:
        marks.append(f"overlap: {_paths(item['paths'])} ({one_line(item['branch'])})")
    for item in row["append_only"]:
        marks.append(f"append-only: {_paths(item['paths'])} ({one_line(item['branch'])})")
    for item in row["records"]:
        marks.append(f"ADR {item['number']} also taken by {', '.join(one_line(b) for b in item['also'])}")
    return "; ".join(marks)


def render_board(data: dict[str, Any]) -> str:
    rows = data["rows"]
    lines: list[str] = []
    if not rows:
        lines.append(f"No worktree branches besides {one_line(data['base'])}.")
    table = [
        [
            one_line(row["branch"]),
            _checks_text(row["checks"]),
            _review_text(row["review"]),
            "land:" + (row["land"]["result"] if row["land"] else "-"),
            _marks_text(row),
        ]
        for row in rows
    ]
    widths = [max((len(cells[i]) for cells in table), default=0) for i in range(4)]
    for cells in table:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells[:4])) + "  " + cells[4])
    for item in data["skipped"]:
        if item["reason"] == "the base branch":
            continue  # the lead's own checkout: expected, not news
        who = one_line(item["branch"]) or one_line(item["path"])
        lines.append(f"skipped: {who} ({item['reason']})")
    for warning in data["warnings"]:
        lines.append(f"note: {warning}")
    return "\n".join(line.rstrip() for line in lines)
