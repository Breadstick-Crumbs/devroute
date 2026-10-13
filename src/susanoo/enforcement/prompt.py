"""The user's latest message in a session, as automatic routing saw it.

Written by the ``UserPromptSubmit`` hook for every prompt while automatic routing is active, and
read when Claude runs ``susanoo route`` itself or picks a profile. It holds the keyword floor of
the message (levels and signal ids, never the text), whether the message asked for autonomy, and
any workflow the user named, so that:

- a route Claude describes in its own words is never lower than the user's message alone;
- only the user can choose a lighter workflow than Susanoo's.

One file per session, overwritten on every prompt.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from susanoo.analyzer.models import Floor
from susanoo.paths import records_dir

# A workflow the user named in their own message: `--profile fast`, "the fast workflow",
# "profile: fast". Not "a fast route" or "fast mode".
_NAMED = re.compile(
    r"--profile[=\s]+[\"']?(fast|standard|deep)\b"
    r"|\b(fast|standard|deep)\s+(?:workflow|profile)\b"
    r"|\b(?:workflow|profile)\s*[:=]\s*(fast|standard|deep)\b",
    re.IGNORECASE,
)
# "don't ever use the fast workflow", "avoid the fast workflow", "deep instead of the fast
# workflow": a negated name does not count.
_NEGATED = re.compile(
    r"\b(?:not|never|no|don.?t|without|avoid|skip|instead of|rather than|no need for)\s+(?:\w+\s+){0,3}$",
    re.IGNORECASE,
)


def session_file(root: Path, session: str, suffix: str = "") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:80] or "no-session"
    return records_dir(root) / "hooks" / f"{safe}{suffix}.json"


def named_profiles(message: str) -> list[str]:
    """Workflows the user named in ``message`` (lowercase, in order, without repeats)."""
    found: list[str] = []
    for m in _NAMED.finditer(message):
        if _NEGATED.search(message[max(0, m.start() - 40) : m.start()]):
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
