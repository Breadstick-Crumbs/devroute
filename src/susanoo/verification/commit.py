"""Bind evidence to the commit it was recorded on (ADR 0015).

Every evidence record carries ``head``, the commit checked out where the checks ran (``git
rev-parse HEAD``), and ``dirty``, whether tracked files or untracked files that are not ignored
differed from it. Susanoo reads both itself; they are never taken from the agent. ``None`` means
unknown: the folder is not in a git repository, git is missing, or git failed. Records written
before these fields existed lack them, which also means unknown.
"""

from __future__ import annotations

import os
import shutil

# Bandit B404: reading the checkout's state with git, argv lists only, never a shell.
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_SECONDS = 60

# Variables that point git at another repository, index, or object store than the folder's own:
# a hook or wrapper may have set them, and the state must be the checkout's.
_REPOSITORY_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


@dataclass(frozen=True)
class TreeState:
    """The commit a checkout is on and whether its files differ from it (None: unknown)."""

    head: str | None
    dirty: bool | None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {"head": self.head, "dirty": self.dirty}


UNKNOWN = TreeState(None, None)


def _git(root: Path, *args: str) -> str | None:
    """Stdout of a read-only git command in ``root``, or None when it cannot run or fails."""
    git = shutil.which("git")
    if git is None:
        return None
    env = {k: v for k, v in os.environ.items() if k not in _REPOSITORY_VARIABLES}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        proc = subprocess.run(  # nosec B603 - argv list, no shell
            [git, "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def tree_state(root: Path) -> TreeState:
    """The commit checked out at ``root`` and whether its files differ from it. Never raises."""
    if not root.is_dir():
        return UNKNOWN
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    head = head.strip().lower() if head else None
    if not head or not all(c in "0123456789abcdef" for c in head) or len(head) not in (40, 64):
        return UNKNOWN
    # Untracked files count unless ignored: a new source file the checks ran on is not in HEAD.
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    return TreeState(head, None if status is None else bool(status.strip("\0\n")))


def across(before: TreeState, after: TreeState) -> TreeState:
    """The state checks that ran between ``before`` and ``after`` saw.

    The commit is the one checked out when they started. They saw a clean tree only when it was
    clean both before and after and HEAD did not move; a move or a change on either side makes it
    dirty, and anything unknown with no change seen leaves it unknown.
    """
    if before.head is None:
        return UNKNOWN
    if after.head is not None and after.head != before.head:
        return TreeState(before.head, True)
    if before.dirty or after.dirty:
        return TreeState(before.head, True)
    if before.dirty is None or after.dirty is None or after.head is None:
        return TreeState(before.head, None)
    return TreeState(before.head, False)
