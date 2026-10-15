"""Which checks `susanoo land` runs on a tree, and how.

The checks are the ready, required steps of the project's DEEP verification plan (what
``susanoo verify --execute --required-only`` would run), adjusted by ``[[land.checks]]``, plus
``[[land.compare]]`` entries that compare a JSON-printing command between the base and the
combined tree. Commands run without a shell through the verification executor.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import shutil

# Bandit B404: compare commands are project checks, like the verification executor's.
import subprocess  # nosec B404
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from susanoo import __version__
from susanoo.config import Config, ConfigError
from susanoo.discovery.executables import project_dirs, resolve_executable
from susanoo.verification.executor import (
    KILL_GRACE_SECONDS,
    OUTPUT_TAIL_CHARS,
    _kill_tree,
    redact,
    run_command,
)

PLAN_TASK = "Check the combined tree of several branches before they land on the base branch"
TREE = "{tree}"


@dataclass
class Check:
    id: str
    command: list[str]
    source: str  # verification | land

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Compare:
    id: str
    command: list[str]
    paths: list[str]
    no_drop: list[str]
    no_rise: list[str]

    def applies(self, changed: list[str]) -> bool:
        return any(_path_matches(p, pattern) for p in changed for pattern in self.paths)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LandSettings:
    checks: list[Check]
    compares: list[Compare]
    setup: list[list[str]]
    env: dict[str, str]
    timeout: float
    keep_both: Any = None  # raw rules, parsed by keepboth.parse_rules
    skipped: list[str] = field(default_factory=list)  # required steps Susanoo cannot run
    require_review: bool = True  # --merge needs a passing code-review of each candidate's tip

    def key(self, tools: dict[str, str]) -> str:
        """Fingerprint of everything that decides a result besides the trees."""
        data = {
            "susanoo": __version__,
            "checks": [c.command for c in self.checks],
            "compare": [c.to_dict() for c in self.compares],
            "setup": self.setup,
            "env": self.env,
            "tools": tools,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class CheckResult:
    id: str
    status: str  # passed | failed | error | timeout
    command: list[str]
    duration_s: float
    tree: str  # combined | rerun | prefix:<k> | alone:<k> | base
    exit_code: int | None = None
    output_tail: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def _path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return path == pattern or fnmatch.fnmatchcase(path, pattern)


def _argv(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ConfigError(f"{where}: `command` must be a non-empty list of strings")
    return list(value)


def _strings(value: Any, where: str, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{where}: `{name}` must be a list of strings")
    return list(value)


def load_settings(config: Config, plan_steps: list[Any], timeout: float) -> LandSettings:
    """Build the check list from verification steps and ``[land]`` configuration."""
    land = config.get("land", {})
    if not isinstance(land, dict):
        raise ConfigError("[land] must be a table")
    checks: list[Check] = []
    skipped: list[str] = []
    use_verification = land.get("use_verification", True)
    if not isinstance(use_verification, bool):
        raise ConfigError("[land] use_verification must be true or false")
    for step in plan_steps if use_verification else []:
        if step.level != "required":
            continue
        if step.mode == "ready" and step.command:
            checks.append(Check(step.id, list(step.command), "verification"))
        elif step.mode != "manual":
            skipped.append(step.id)
    for i, item in enumerate(land.get("checks", []) or []):
        where = f"[[land.checks]] entry {i + 1}"
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ConfigError(f"{where}: `id` must be a non-empty string")
        existing = next((c for c in checks if c.id == item["id"]), None)
        if item.get("enabled") is False:
            checks = [c for c in checks if c.id != item["id"]]
            continue
        skipped = [x for x in skipped if x != item["id"]]
        command = _argv(item.get("command"), where)
        if existing is not None:
            existing.command, existing.source = command, "land"
        else:
            checks.append(Check(item["id"], command, "land"))
    # The same command twice (unit and integration tests are often one suite) runs once.
    unique: list[Check] = []
    seen: set[tuple[str, ...]] = set()
    for check in checks:
        if tuple(check.command) not in seen:
            seen.add(tuple(check.command))
            unique.append(check)
    compares: list[Compare] = []
    for i, item in enumerate(land.get("compare", []) or []):
        where = f"[[land.compare]] entry {i + 1}"
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ConfigError(f"{where}: `id` must be a non-empty string")
        if item.get("enabled") is False:
            continue
        compare = Compare(
            id=item["id"],
            command=_argv(item.get("command"), where),
            paths=_strings(item.get("paths"), where, "paths"),
            no_drop=_strings(item.get("no_drop"), where, "no_drop"),
            no_rise=_strings(item.get("no_rise"), where, "no_rise"),
        )
        if not compare.paths:
            raise ConfigError(f"{where}: `paths` must name the paths whose changes trigger it")
        if not compare.no_drop and not compare.no_rise:
            raise ConfigError(f"{where}: give `no_drop` or `no_rise` keys to compare")
        compares.append(compare)
    setup = [_argv(cmd, f"[land] setup entry {i + 1}") for i, cmd in enumerate(land.get("setup", []) or [])]
    env = land.get("env", {}) or {}
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ConfigError("[land] env must be a table of strings")
    require_review = land.get("require_review", True)
    if not isinstance(require_review, bool):
        raise ConfigError("[land] require_review must be true or false")
    return LandSettings(
        unique, compares, setup, dict(env), timeout, land.get("keep_both"), skipped, require_review
    )


def _expand(value: str, tree: Path) -> str:
    return value.replace(TREE, str(tree))


@contextlib.contextmanager
def _environ(env: dict[str, str], tree: Path) -> Iterator[None]:
    """Set ``env`` (with ``{tree}`` expanded) for the commands run inside this block."""
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            os.environ[k] = _expand(v, tree)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


class Runner:
    """Runs checks in a scratch tree, finding executables in the user's checkouts first."""

    def __init__(self, settings: LandSettings, tool_roots: list[Path]) -> None:
        self.settings = settings
        self.tool_roots = tool_roots
        self.seconds = 0.0

    def resolve(self, argv: list[str], tree: Path) -> list[str]:
        name = _expand(argv[0], tree)
        rest = [_expand(a, tree) for a in argv[1:]]
        if os.path.isabs(name):
            return [name, *rest]
        # Every checkout's own environment before PATH: a scratch tree has none, and the main
        # worktree's .venv usually holds the project's tools.
        for root in self.tool_roots:
            for directory in project_dirs(root):
                found = shutil.which(name, path=str(directory))
                if found:
                    return [found, *rest]
        found = resolve_executable(name, None)
        return [found or name, *rest]  # the executor reports "not found"

    def tools(self, tree: Path) -> dict[str, str]:
        names = {c.command[0] for c in self.settings.checks} | {c.command[0] for c in self.settings.compares}
        names |= {cmd[0] for cmd in self.settings.setup}
        return {n: self.resolve([n], tree)[0] for n in sorted(names)}

    def setup(self, tree: Path) -> CheckResult | None:
        """Run ``[land] setup``; the first failure is returned."""
        for i, cmd in enumerate(self.settings.setup):
            result = self._run(f"setup-{i + 1}", cmd, tree, "setup")
            if not result.ok:
                return result
        return None

    def run(self, check: Check, tree: Path, label: str) -> CheckResult:
        return self._run(check.id, check.command, tree, label)

    def _run(self, check_id: str, command: list[str], tree: Path, label: str) -> CheckResult:
        argv = self.resolve(command, tree)
        with _environ(self.settings.env, tree):
            status, code, duration, tail = run_command(argv, tree, self.settings.timeout)
        self.seconds += duration
        return CheckResult(check_id, status, command, round(duration, 2), label, code, tail)

    def measure(self, compare: Compare, tree: Path) -> tuple[dict[str, float] | None, float, str]:
        """Run a compare command; return its watched numbers, the duration, and an error."""
        argv = self.resolve(compare.command, tree)
        started = time.monotonic()
        # Its own process group, so a timeout stops the whole tree, as the executor does.
        group: dict[str, Any]
        if sys.platform == "win32":
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            group = {"start_new_session": True}
        try:
            with _environ(self.settings.env, tree):
                # Bandit B603: argv list from project configuration, no shell.
                proc = subprocess.Popen(  # nosec B603
                    argv,
                    cwd=tree,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **group,
                )
        except OSError as exc:
            return None, 0.0, f"could not run: {exc}"
        try:
            stdout, stderr = proc.communicate(timeout=self.settings.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=KILL_GRACE_SECONDS)
            duration = time.monotonic() - started
            self.seconds += duration
            return None, duration, f"did not finish within {self.settings.timeout:g} s"
        duration = time.monotonic() - started
        self.seconds += duration
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            tail = redact((stderr or stdout or "")[-OUTPUT_TAIL_CHARS:])
            return None, duration, f"exit {proc.returncode}, output is not JSON: {tail.strip()[-300:]}"
        values: dict[str, float] = {}
        for key in (*compare.no_drop, *compare.no_rise):
            value: Any = data
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if not isinstance(value, int | float) or isinstance(value, bool):
                return None, duration, f"`{key}` is not a number in the output"
            values[key] = float(value)
        return values, duration, ""


def compare_values(compare: Compare, base: dict[str, float], head: dict[str, float]) -> list[str]:
    """Human-readable regressions (empty when the head is at least as good as the base)."""
    problems: list[str] = []
    for key in compare.no_drop:
        if head[key] < base[key]:
            problems.append(f"{key} dropped from {base[key]:g} to {head[key]:g}")
    for key in compare.no_rise:
        if head[key] > base[key]:
            problems.append(f"{key} rose from {base[key]:g} to {head[key]:g}")
    return problems
