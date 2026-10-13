"""Opt-in workflow enforcement through Claude Code hooks.

Names are imported on first use, so a hook loads only the modules it needs.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "Decision": "gates",
    "pre_tool_use": "gates",
    "session_start": "gates",
    "stop": "gates",
    "summary": "gates",
    "HOOK_EVENTS": "hooks",
    "handle": "hooks",
    "RouteState": "state",
    "active_route": "state",
    "evidence_status": "state",
    "route_state": "state",
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
