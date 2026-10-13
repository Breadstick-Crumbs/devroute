"""Run verification commands and capture machine-readable evidence.

Evidence has a ``source``: ``executed`` means Susanoo ran the command and observed
the exit code itself; ``reported`` means an agent or person told Susanoo the
result (``susanoo evidence``). Reports are never upgraded to executed results.

Commands come from the packaged tool registry as argv lists and run without a
shell, in the project root. Some of them (``npm test``, ``make test``, pytest)
execute the project's own code, so ``--execute`` is opt-in and intended for
repositories you trust.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal

# Bandit B404: executing verification commands is this module's purpose.
import subprocess  # nosec B404
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo.discovery.executables import resolve_executable
from susanoo.verification.planner import VerificationPlan, VerificationStep

OUTPUT_TAIL_CHARS = 2000
KILL_GRACE_SECONDS = 5
EVIDENCE_STATUSES = ("passed", "failed", "error", "timeout", "skipped")

# key = value / "key": "value" / Authorization: Bearer x -> everything after the separator
# up to the end of the line is masked.
_KEY_VALUE = re.compile(
    r"(?im)\b([\w.-]*(?:api[_-]?key|token|secret|passw(?:or)?d|passwd|authorization|"
    r"access[_-]?key|private[_-]?key|credential)[\w.-]*[\"']?)(\s*[:=]\s*)([^\r\n]+)"
)
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@")
_TOKEN_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str) -> str:
    """Mask common credential formats before evidence is stored. Best effort, not a guarantee."""
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


@dataclass
class Evidence:
    step: str
    capability: str
    status: str  # passed | failed | error | timeout | skipped
    source: str  # executed | reported
    command: list[str] | None
    exit_code: int | None
    duration_s: float | None
    recorded_at: str
    output_tail: str = ""
    note: str = ""
    record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_command(argv: list[str], cwd: Path, timeout: float) -> tuple[str, int | None, float, str]:
    """Run ``argv`` in ``cwd``; return (status, exit_code, duration_seconds, output_tail)."""
    executable = resolve_executable(argv[0], cwd)
    if executable is None:
        return "error", None, 0.0, f"executable not found on PATH: {argv[0]}"
    started = time.monotonic()
    # Run in a new process group/session so a timeout can stop the whole tree
    # (test runners often spawn children that would otherwise keep running).
    group: dict[str, Any]
    if sys.platform == "win32":
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    try:
        # Bandit B603: argv list from the tool registry, no shell.
        proc = subprocess.Popen(  # nosec B603
            [executable, *argv[1:]],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **group,
        )
    except OSError as exc:
        return "error", None, time.monotonic() - started, f"could not start: {exc}"
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            output, _ = proc.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            output = ""
        tail = redact((output or "")[-OUTPUT_TAIL_CHARS:])
        return "timeout", None, time.monotonic() - started, tail
    duration = time.monotonic() - started
    status = "passed" if proc.returncode == 0 else "failed"
    return status, proc.returncode, duration, redact((output or "")[-OUTPUT_TAIL_CHARS:])


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate ``proc`` and its descendants."""
    try:
        if sys.platform == "win32":
            taskkill = shutil.which("taskkill")
            if taskkill:
                # Bandit B603: fixed system utility with a numeric pid argument.
                subprocess.run(  # nosec B603
                    [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=KILL_GRACE_SECONDS,
                    check=False,
                )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        proc.kill()


def execute_plan(
    plan: VerificationPlan,
    root: Path,
    *,
    timeout: float = 900,
    only: set[str] | None = None,
    required_only: bool = False,
) -> list[Evidence]:
    """Execute ``ready`` steps; every other selected step gets a ``skipped`` entry explaining why."""
    evidence: list[Evidence] = []
    cache: dict[tuple[str, ...], Evidence] = {}
    for step in plan.steps:
        if only is not None and step.id not in only:
            continue
        if required_only and step.level != "required":
            continue
        evidence.append(_execute_step(step, root, timeout, cache))
    return evidence


def _execute_step(
    step: VerificationStep, root: Path, timeout: float, cache: dict[tuple[str, ...], Evidence]
) -> Evidence:
    if step.mode != "ready" or not step.command:
        reason = "performed by the agent or a person" if step.mode == "manual" else "no usable tool"
        return Evidence(
            step=step.id,
            capability=step.capability,
            status="skipped",
            source="executed",
            command=None,
            exit_code=None,
            duration_s=None,
            recorded_at=_now(),
            note=f"not run by Susanoo: {reason}",
        )
    key = tuple(step.command)
    if key in cache:
        prior = cache[key]
        return Evidence(
            step=step.id,
            capability=step.capability,
            status=prior.status,
            source="executed",
            command=step.command,
            exit_code=prior.exit_code,
            duration_s=0.0,
            recorded_at=_now(),
            note=f"same command as step '{prior.step}'; result reused",
        )
    status, code, duration, tail = run_command(step.command, root, timeout)
    result = Evidence(
        step=step.id,
        capability=step.capability,
        status=status,
        source="executed",
        command=step.command,
        exit_code=code,
        duration_s=round(duration, 2),
        recorded_at=_now(),
        output_tail=tail,
    )
    cache[key] = result
    return result


def reported_evidence(step: str, status: str, command: str | None = None, note: str = "") -> Evidence:
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(EVIDENCE_STATUSES)}")
    return Evidence(
        step=step,
        capability="",
        status=status,
        source="reported",
        command=[redact(command)] if command else None,
        exit_code=None,
        duration_s=None,
        recorded_at=_now(),
        note=redact(note),
    )
