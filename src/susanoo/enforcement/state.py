"""Workflow state derived from local records: active route, approval, and evidence status."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from susanoo.enforcement.prompt import session_file
from susanoo.evaluation.store import RecordStore, sibling_stores
from susanoo.paths import is_initialized


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

    Runs in another checkout of the repository (a git worktree, ADR 0017) carry ``checkout``.
    The latest run counts per checkout, and a step passes only when it passes in every checkout
    that ran it, so a pass in one checkout never clears a failure in another.
    """
    record_id = route.get("id")
    executed: dict[tuple[str, str], str] = {}  # (step, checkout) -> latest executed status
    latest_any: dict[str, str] = {}
    for item in evidence:
        if item.get("record_id") != record_id:
            continue
        step = str(item.get("step"))
        status = str(item.get("status"))
        if item.get("source") == "executed" and status != "skipped":
            executed[(step, str(item.get("checkout") or ""))] = status
            latest_any[step] = status
        elif item.get("source") == "reported":
            latest_any[step] = status
    latest_executed: dict[str, str] = {}
    for (step, _), status in executed.items():
        if latest_executed.get(step, "passed") == "passed":
            latest_executed[step] = status
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


# Steps a recorded review stands for (ADR 0015), by the tools whose review they need. With review
# records given, such a step holds only when the newest review of the tip by each tool passed: a
# reported result is bound to the commit it was reported on, not the commit that was reviewed.
REVIEW_STEPS: dict[str, tuple[str, ...]] = {"independent_review": ("code-review",)}
# A route carrying this control needs a passing /security-review of the tip for those steps too.
SECURITY_REVIEW_CONTROL = "security-review"


def review_tools(route: dict[str, Any], step_id: str) -> tuple[str, ...]:
    """The review tools whose passing review of the tip ``step_id`` needs, or () for none."""
    tools = REVIEW_STEPS.get(step_id, ())
    if tools and SECURITY_REVIEW_CONTROL in [str(c) for c in route.get("controls", []) or []]:
        tools = (*tools, "security-review")
    return tools


def _demanded(route: dict[str, Any]) -> list[dict[str, Any]]:
    """The required steps the evidence gate asks evidence for (see ``evidence_status``)."""
    return [
        step
        for step in route.get("verification", [])
        if step.get("level") == "required"
        and not (route.get("auto") is True and step.get("mode") == "unavailable")
    ]


def unverified_steps(
    route: dict[str, Any],
    evidence: list[dict[str, Any]],
    tip: str | None,
    reviews: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Required steps of ``route`` whose checks do not hold for commit ``tip`` (ADR 0015).

    A step holds when the evidence gate accepts it and its newest evidence the gate counts was
    recorded on ``tip`` (``head``, a full commit id) with no uncommitted changes (``dirty`` false):
    for a step Susanoo can run, a passing executed run; for a step it cannot, a reported pass or
    explained skip, as the gate asks. Evidence without ``head`` or ``dirty``, including every
    record written before they existed, never holds. ``tip`` None (unknown) holds nothing.

    With ``reviews`` (review records, see ``land.reviews``), a step a review stands for
    (``review_tools``) holds instead when the gate accepts it and the newest review of ``tip`` by
    each of its tools passed; its reported evidence then needs no ``head``, and an explained skip
    or a review of another commit does not count. Never looser than the gate: a step the gate
    refuses never holds.
    """
    demanded = _demanded(route)
    missing, failing = evidence_status(route, evidence)
    if not tip:
        return [str(step.get("id")) for step in demanded]
    tip = tip.lower()
    record_id = route.get("id")
    newest: dict[str, dict[str, Any]] = {}
    ready = {str(step.get("id")) for step in demanded if step.get("mode") == "ready"}
    for item in evidence:
        if item.get("record_id") != record_id:
            continue
        step_id = str(item.get("step"))
        executed = item.get("source") == "executed" and item.get("status") != "skipped"
        # Exactly what the gate counts: executed runs for runnable steps, reports too for others.
        if executed or (step_id not in ready and item.get("source") == "reported"):
            newest[step_id] = item
    unverified: list[str] = []
    for step in demanded:
        step_id = str(step.get("id"))
        found = newest.get(step_id)
        accepted = ("passed",) if step_id in ready else ("passed", "skipped")
        gate = found is not None and step_id not in missing and step_id not in failing
        tools = review_tools(route, step_id) if reviews is not None else ()
        if tools:
            holds = gate and all(_review_passed(reviews or [], tip, tool) for tool in tools)
        else:
            head = found.get("head") if found else None
            holds = (
                gate
                and found is not None
                and found.get("status") in accepted
                and isinstance(head, str)
                and head.lower() == tip
                and found.get("dirty") is False
            )
        if not holds:
            unverified.append(step_id)
    return unverified


def _review_passed(reviews: list[dict[str, Any]], tip: str, tool: str) -> bool:
    from susanoo.land.reviews import review_for  # only when reviews are given: hooks stay light

    found = review_for(reviews, tip, tool)
    return found is not None and found.get("status") == "passed"


def is_verified(
    route: dict[str, Any],
    evidence: list[dict[str, Any]],
    tip: str | None,
    reviews: list[dict[str, Any]] | None = None,
) -> bool:
    """Whether ``route``'s checks hold for commit ``tip``: ADR 0015's "verified".

    With ``reviews``, the steps a review stands for need a passing review of ``tip`` (see
    ``unverified_steps``). A route with no required step is never verified, because nothing
    bound it to a commit.
    """
    return bool(tip) and bool(_demanded(route)) and not unverified_steps(route, evidence, tip, reviews)


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


def find_active_route(root: Path, session_id: str | None) -> tuple[RecordStore, RouteState] | None:
    """The session's open route in ``root``'s records or another checkout of its repository.

    Each checkout keeps its own records and its own gates (ADR 0017): the route is often recorded
    in the main checkout while the work happens in a git worktree. Every checkout's active route
    of ``session_id`` counts. A route that a checkout's hooks are tracking wins, because that is
    the one a Stop hook enforces; then the newest (this checkout's on a tie). Routes of other
    sessions are never active. Returns the store that holds the route: whatever replaces it
    belongs there, where that checkout's Stop hook reads it.
    """
    if not session_id:
        return None
    stores = [RecordStore(root)] if is_initialized(root) else []
    hits: list[tuple[bool, str, RecordStore, RouteState]] = []
    for store in [*stores, *sibling_stores(root)]:
        state = active_route(store, session_id)
        if state is not None:
            tracked = _hook_record(store.root, session_id) == state.record_id
            hits.append((tracked, str(state.route.get("created_at", "")), store, state))
    if not hits:
        return None
    # `max` keeps the first of equals: this checkout's route on a tie
    _, _, store, state = max(hits, key=lambda hit: (hit[0], hit[1]))
    return store, state


def _hook_record(root: Path, session_id: str) -> object:
    """The route the hooks in ``root`` last saw for the session (see ``hooks.SessionState``)."""
    try:
        data = json.loads(session_file(root, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("record_id") if isinstance(data, dict) else None
