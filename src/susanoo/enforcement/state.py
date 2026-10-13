"""Workflow state derived from local records: active route, approval, and evidence status."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from susanoo.evaluation.store import RecordStore


@dataclass
class RouteState:
    """Everything the enforcement gates need to know about one route."""

    route: dict[str, Any]
    approved: bool
    closed: bool
    missing: list[str] = field(default_factory=list)  # required steps without acceptable evidence
    failing: list[str] = field(default_factory=list)  # required ready steps whose latest run failed

    @property
    def record_id(self) -> str:
        return str(self.route.get("id"))

    @property
    def profile(self) -> str:
        return str(self.route.get("profile", ""))

    @property
    def controls(self) -> list[str]:
        return [str(c) for c in self.route.get("controls", [])]


def evidence_status(route: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return (missing, failing) required verification steps for ``route``.

    - ``ready`` steps (Susanoo can run them) need *executed* evidence; the latest run must pass.
    - ``manual`` and ``unavailable`` steps need any recorded evidence, so the agent must at least
      report what was done or explicitly record that the step was skipped and why.
    """
    record_id = route.get("id")
    latest_executed: dict[str, str] = {}
    latest_any: dict[str, str] = {}
    for item in evidence:
        if item.get("record_id") != record_id:
            continue
        step = str(item.get("step"))
        status = str(item.get("status"))
        if item.get("source") == "executed" and status != "skipped":
            latest_executed[step] = status
            latest_any[step] = status
        elif item.get("source") == "reported":
            latest_any[step] = status
    missing: list[str] = []
    failing: list[str] = []
    for step in route.get("verification", []):
        if step.get("level") != "required":
            continue
        if route.get("auto") is True and step.get("mode") == "unavailable":
            continue  # automatic routes: a check whose tool is missing is reported, not demanded
        step_id = str(step.get("id"))
        # Runnable steps need a passing run that Susanoo observed; others need a reported
        # result that is passing or an explicit, explained skip.
        result: str | None = (
            latest_executed.get(step_id) if step.get("mode") == "ready" else latest_any.get(step_id)
        )
        accepted = ("passed",) if step.get("mode") == "ready" else ("passed", "skipped")
        if result is None:
            missing.append(step_id)
        elif result not in accepted:
            failing.append(step_id)
    return missing, failing


def route_state(store: RecordStore, route: dict[str, Any]) -> RouteState:
    record_id = route.get("id")
    approved = any(a.get("record_id") == record_id for a in store.read("approvals"))
    closed = any(o.get("record_id") == record_id for o in store.read("outcomes"))
    missing, failing = evidence_status(route, store.read("evidence"))
    return RouteState(route=route, approved=approved, closed=closed, missing=missing, failing=failing)


def active_route(store: RecordStore, session_id: str | None) -> RouteState | None:
    """The most recent open route created in ``session_id``, if any.

    Routes from other sessions (or with no session) are never active, so enforcement
    only affects sessions that actually used /susanoo.
    """
    if not session_id:
        return None
    routes = [r for r in store.read("routes") if r.get("session_id") == session_id]
    if not routes:
        return None
    state = route_state(store, routes[-1])
    return None if state.closed else state
