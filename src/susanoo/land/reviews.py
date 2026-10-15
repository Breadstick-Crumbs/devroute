"""Review records (ADR 0015): which commit a `/code-review` or `/security-review` saw, and its result.

The review itself is Claude Code's built-in command; Susanoo only records it, against the exact
commit it saw. Recording refuses a commit that is not the branch's current tip, so a review of an
older commit is never recorded as a review of a newer one. Records go to
``<git common dir>/susanoo/records/reviews.jsonl``, next to ``land.jsonl`` and shared by every
worktree, because the session that reviews and the one that wrote the branch live in different
worktrees. Nothing here is a gate yet: ``land --merge`` checking them is a later slice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo.evaluation.store import new_record_id
from susanoo.land.gitops import Git, LandError, common_dir, gh_available, gh_json, pick_tip
from susanoo.land.records import append_record, read_records
from susanoo.verification.executor import redact

REVIEWS_FILE = "reviews.jsonl"
REVIEW_STATUSES = ("passed", "failed")
REVIEW_TOOLS = ("code-review", "security-review")
MIN_TIP_CHARS = 7
NOTE_CHARS = 500

_HEX = re.compile(r"[0-9a-f]+")
_FULL_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


@dataclass(frozen=True)
class ReviewTarget:
    """A pull request or branch and its current tip."""

    spec: str
    branch: str
    pr: int | None
    tip: str

    @property
    def label(self) -> str:
        return f"#{self.pr}" if self.pr is not None else self.branch


def repository(root: Path) -> Path:
    """The git common dir of ``root``'s repository, where review records live."""
    try:
        return common_dir(root)
    except LandError as exc:
        raise LandError(f"{root} is not inside a git repository") from exc


def resolve_target(root: Path, spec: str, remote: str = "origin") -> ReviewTarget:
    """The current tip of a pull request (``52``, ``#52``: its head on GitHub, through ``gh``) or of
    a branch (its local or remote copy, whichever contains the other, as ``susanoo land`` picks it)."""
    repository(root)  # outside a repository, say so before anything else
    value = spec.strip()
    number = value[1:] if value.startswith("#") else value
    if number.isascii() and number.isdigit():
        if not gh_available():
            raise LandError(f"pull request {number} needs the GitHub CLI `gh`, which was not found")
        data = gh_json(root, "pr", "view", number, "--json", "number,headRefName,headRefOid")
        head = str(data.get("headRefOid") or "").lower() if isinstance(data, dict) else ""
        if not isinstance(data, dict) or not _FULL_SHA.fullmatch(head):
            raise LandError(f"gh returned no head commit for pull request {number}")
        return ReviewTarget(value, str(data.get("headRefName") or ""), int(number), head)
    repo = Git(root)
    if (
        not value
        or value.startswith("-")
        or repo.run("check-ref-format", "--branch", value, check=False).returncode
    ):
        raise LandError(f"{spec!r} is not a pull request number or a valid branch name")
    if remote.startswith("-"):
        raise LandError(f"{remote!r} is not a remote name")
    local = repo.sha(f"refs/heads/{value}")
    remote_tip = _remote_tip(repo, value, remote)
    return ReviewTarget(value, value, None, pick_tip(repo, value, local, remote_tip, remote).lower())


def _remote_tip(repo: Git, branch: str, remote: str) -> str | None:
    """The branch's tip on ``remote``, fetched now; None only when the remote surely lacks it.

    A repository without that remote, or a remote without the branch, has no remote copy. A remote
    that cannot be reached is refused rather than skipped: a newer commit may be there, and an
    older local copy must not pass for the current tip.
    """
    if repo.run("remote", "get-url", "--", remote, check=False).returncode != 0:
        return None  # no such remote: a local-only branch
    listed = repo.run(
        "ls-remote", "--exit-code", "--heads", "--", remote, f"refs/heads/{branch}", check=False
    )
    if listed.returncode == 2:
        return None  # reached, and the branch is not there (never pushed)
    unreachable = (
        f"could not reach {remote} to confirm the current tip of {branch}; try again when it is reachable"
    )
    if listed.returncode != 0:
        raise LandError(unreachable)
    fetched = repo.run(
        "fetch",
        "--no-tags",
        "--",
        remote,
        f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",
        check=False,
    )
    tip = repo.sha(f"refs/remotes/{remote}/{branch}") if fetched.returncode == 0 else None
    if tip is None:
        raise LandError(unreachable)
    return tip


def check_tip(given: str, target: ReviewTarget) -> None:
    """Refuse ``given`` unless it names ``target``'s current tip (a full id or a 7+ digit prefix)."""
    tip = given.strip().lower()
    if len(tip) < MIN_TIP_CHARS or not _HEX.fullmatch(tip):
        raise LandError(f"--tip must be a commit id of at least {MIN_TIP_CHARS} hex digits, not {given!r}")
    if not target.tip.startswith(tip):
        raise LandError(
            f"{target.label} is at {target.tip[:12]}, not {tip[:12]}: the review saw an older or other "
            "commit; review the current tip and record that"
        )


def record_review(
    root: Path,
    target: ReviewTarget,
    *,
    tool: str,
    status: str,
    note: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    if tool not in REVIEW_TOOLS:
        raise LandError(f"--tool must be one of {', '.join(REVIEW_TOOLS)}")
    if status not in REVIEW_STATUSES:
        raise LandError(f"--status must be one of {', '.join(REVIEW_STATUSES)}")
    record: dict[str, Any] = {
        "kind": "review",
        "id": new_record_id(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": target.spec,
        "branch": target.branch,
        "pr": target.pr,
        "tip": target.tip,
        "tool": tool,
        "status": status,
        "note": redact(" ".join(note.split()))[:NOTE_CHARS],
        "session_id": session_id,
        "checkout": str(root),
    }
    append_record(repository(root), record, REVIEWS_FILE)
    return record


def read_reviews(common: Path) -> list[dict[str, Any]]:
    """Every review record, oldest first; unreadable lines and other kinds are skipped."""
    return [r for r in read_records(common, REVIEWS_FILE) if r.get("kind") == "review"]


def matching(reviews: list[dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    """Reviews of a pull request number or a branch name, without looking either up."""
    value = spec.strip()
    number = value[1:] if value.startswith("#") else value
    if number.isascii() and number.isdigit():
        return [r for r in reviews if r.get("pr") == int(number)]
    return [r for r in reviews if r.get("branch") == value]


def review_for(reviews: list[dict[str, Any]], tip: str, tool: str = "code-review") -> dict[str, Any] | None:
    """The newest review by ``tool`` of exactly commit ``tip``, or None. Its status decides."""
    tip = tip.lower()
    for review in reversed(reviews):
        if review.get("tool") == tool and str(review.get("tip") or "").lower() == tip:
            return dict(review)
    return None
