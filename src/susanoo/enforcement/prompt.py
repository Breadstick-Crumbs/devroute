"""The user's latest message in a session, as automatic routing saw it.

Written by the ``UserPromptSubmit`` hook for every prompt while automatic routing is active, and
read when Claude runs ``susanoo route`` itself or picks a profile. It holds the keyword floor of
the message (levels and signal ids, never the text), whether the message asked for autonomy, and
any workflow the user named, so that:

- a route Claude describes in its own words is never lower than the user's message alone;
- only the user can choose a lighter workflow than Susanoo's.

One file per session, written only by the prompt hook and overwritten on every prompt. A prompt
made only of the app's blocks (another session's note, a notification; ADR 0016) keeps the user's
floor, autonomy request, and named workflows, and only moves the turn on (id, time, kind). A separate
marker file records that the route-before-edit gate already stopped an edit for that prompt, so
the floor is never rewritten (or lost) by a tool-call hook.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

from susanoo.analyzer.models import Floor
from susanoo.paths import records_dir

# Message kinds from the prompt classifier (``auto.classify``). Defined here, in the light module,
# so the tool-call hooks can test a saved message's kind without importing the router.
SKIP, QUESTION, AMBIGUOUS, TASK = "skip", "question", "ambiguous", "task"

# A workflow the user named in their own message: `--profile fast`, "the fast workflow",
# "profile: fast". Not "a fast route" or "fast mode".
_NAMED = re.compile(
    r"--profile[=\s]+[\"']?(fast|standard|deep)\b"
    r"|\b(fast|standard|deep)\s+(?:workflow|profile)\b"
    r"|\b(?:workflow|profile)\s*[:=]\s*(fast|standard|deep)\b",
    re.IGNORECASE,
)
# A name in a sentence with a negation before it does not count: "I don't think we should use
# the fast workflow", "Please do NOT, under any circumstances, use the fast workflow", "deep
# instead of the fast workflow". Erring this way only means Claude asks the user once more.
_NEGATED = re.compile(
    r"\b(?:not|never|no|cannot|\w+n['\u2019]t"
    r"|(?:do|does|did|ca|wo|would|should|could|is|are|was|were|has|have|had|must)nt"
    r"|without|avoid|skip|instead of|rather than)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?;\n]")


def session_file(root: Path, session: str, suffix: str = "") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:80] or "no-session"
    return records_dir(root) / "hooks" / f"{safe}{suffix}.json"


def named_profiles(message: str) -> list[str]:
    """Workflows the user named in ``message`` (lowercase, in order, without repeats)."""
    found: list[str] = []
    for m in _NAMED.finditer(message):
        if _NEGATED.search(_SENTENCE_END.split(message[: m.start()])[-1]):
            continue
        name = next(g for g in m.groups() if g).lower()
        if name not in found:
            found.append(name)
    return found


def save(root: Path, session: str, data: dict[str, Any]) -> None:
    """Best effort: a failed write leaves no record (no floor, no named profile), never an error."""
    path = session_file(root, session, ".prompt")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink()


def load(root: Path, session: str | None) -> dict[str, Any] | None:
    """The saved record of the session's latest message, or ``None`` (no automatic routing)."""
    if not session:
        return None
    try:
        data = json.loads(session_file(root, session, ".prompt").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError: bad JSON or undecodable bytes
        return None
    return data if isinstance(data, dict) else None


def floor(data: dict[str, Any] | None) -> Floor | None:
    return Floor.from_dict(data.get("floor")) if data else None


def nudged(root: Path, session: str, message_id: str) -> bool:
    """Whether the route-before-edit gate already stopped an edit for message ``message_id``."""
    try:
        data = json.loads(session_file(root, session, ".nudged").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("message") == message_id


def mark_nudged(root: Path, session: str, message_id: str) -> bool:
    """Record the nudge for message ``message_id``; ``False`` when it could not be recorded.

    Written to a temporary file and moved into place, so a parallel hook never reads half of it.
    A newer message makes the mark stale by itself.
    """
    path = session_file(root, session, ".nudged")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"message": message_id}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True
