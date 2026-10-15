"""Thin wrappers around the ``git`` and ``gh`` command-line tools (argv lists, never a shell)."""

from __future__ import annotations

import json
import os
import re
import shutil

# Bandit B404: running git and gh is this module's purpose.
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 600
MIN_GIT = (2, 35)  # merge.conflictStyle = zdiff3

# Applied to every git call: long paths on Windows, no recorded resolutions, quiet detached HEADs.
# No fsmonitor daemon either: on Windows it holds a scratch worktree's folder and blocks its removal.
_BASE_OPTIONS = (
    "-c",
    "core.longpaths=true",
    "-c",
    "rerere.enabled=false",
    "-c",
    "advice.detachedHead=false",
    "-c",
    "core.fsmonitor=false",
)


class LandError(Exception):
    """A user-facing failure of `susanoo land`."""


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise LandError(f"`{name}` was not found on PATH")
    return found


@dataclass
class Git:
    """Run git in ``cwd``. ``hooks_path`` (an empty folder) turns off repository hooks."""

    cwd: Path
    hooks_path: Path | None = None
    conflict_style: bool = False

    def argv(self, *args: str) -> list[str]:
        options = list(_BASE_OPTIONS)
        if self.hooks_path is not None:
            options += ["-c", f"core.hooksPath={self.hooks_path}"]
        if self.conflict_style:
            options += ["-c", "merge.conflictStyle=zdiff3"]
        return [_tool("git"), *options, *args]

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        # Bandit B603: argv list, no shell.
        try:
            proc = self._call(args)
        except subprocess.TimeoutExpired as exc:
            raise LandError(f"git {' '.join(args[:3])} did not finish within {GIT_TIMEOUT} s") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise LandError(f"git {' '.join(args[:3])} failed: {detail[-1] if detail else proc.returncode}")
        return proc

    def _call(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603
            self.argv(*args),
            cwd=self.cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "GIT_MERGE_AUTOEDIT": "no"},
        )

    def out(self, *args: str) -> str:
        return self.run(*args).stdout.strip()

    def sha(self, ref: str) -> str | None:
        proc = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None

    def tree(self, ref: str) -> str:
        return self.out("rev-parse", f"{ref}^{{tree}}")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self.run("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0

    def changed_files(self, a: str, b: str) -> list[str]:
        raw = self.run("diff", "--name-only", "--no-renames", "-z", a, b).stdout
        return [p for p in raw.split("\0") if p]

    def conflicted_files(self) -> list[str]:
        raw = self.run("diff", "--name-only", "--diff-filter=U", "-z").stdout
        return sorted({p for p in raw.split("\0") if p})


def pick_tip(repo: Git, branch: str, local: str | None, remote: str | None, remote_name: str) -> str:
    """A branch's current tip: its local or its remote copy, whichever contains the other."""
    if local and remote:
        if local == remote or repo.is_ancestor(local, remote):
            return remote
        if repo.is_ancestor(remote, local):
            return local
        raise LandError(f"{branch}: the local branch and {remote_name}/{branch} have diverged")
    if local or remote:
        return str(local or remote)
    raise LandError(f"no branch named {branch}, locally or on {remote_name}")


def git_version() -> tuple[int, int]:
    text = Git(Path.cwd()).out("--version")
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def common_dir(root: Path) -> Path:
    """The repository's shared git directory (the same for every worktree)."""
    value = Git(root).out("rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(value)


def main_worktree(root: Path) -> Path | None:
    """The main worktree (where a project's environment usually lives), or None for a bare repo."""
    for path, _branch in worktrees(root):
        return path
    return None


def worktrees(root: Path) -> list[tuple[Path, str | None]]:
    """(path, checked-out branch name) for every worktree; the main worktree comes first."""
    text = Git(root).run("worktree", "list", "--porcelain").stdout
    found: list[tuple[Path, str | None]] = []
    path: Path | None = None
    branch: str | None = None
    bare = False
    for line in [*text.splitlines(), ""]:
        if line.startswith("worktree "):
            path, branch, bare = Path(line[len("worktree ") :]), None, False
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/") :]
        elif line == "bare":
            bare = True
        elif not line and path is not None:
            if not bare:
                found.append((path, branch))
            path = None
    return found


def remove_tree(path: Path, attempts: int = 5) -> bool:
    """Delete a folder, retrying while Windows (antivirus, indexers) briefly holds files."""

    def make_writable(func: Any, target: str, _exc: Any) -> None:
        # Git marks object files read-only, which Windows refuses to delete.
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    for attempt in range(attempts):
        if not path.exists():
            return True
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=make_writable)
        else:  # pragma: no cover - Python 3.11
            shutil.rmtree(path, onerror=make_writable)
        if not path.exists():
            return True
        time.sleep(0.2 * (attempt + 1))
    return not path.exists()


# ---------------------------------------------------------------------------
# GitHub CLI
# ---------------------------------------------------------------------------

PR_FIELDS = "number,title,body,headRefName,headRefOid,baseRefName,state,isDraft,isCrossRepository,url"


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Bandit B603: argv list, no shell.
    try:
        proc = _gh_call(root, args)
    except subprocess.TimeoutExpired as exc:
        raise LandError(f"gh {' '.join(args[:3])} did not finish within {GIT_TIMEOUT} s") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise LandError(f"gh {' '.join(args[:3])} failed: {detail[-1] if detail else proc.returncode}")
    return proc


def _gh_call(root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [_tool("gh"), *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT,
        check=False,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"},
    )


def gh_json(root: Path, *args: str) -> Any:
    try:
        return json.loads(gh(root, *args).stdout or "null")
    except json.JSONDecodeError as exc:
        raise LandError(f"gh {' '.join(args[:3])} returned unreadable JSON") from exc
