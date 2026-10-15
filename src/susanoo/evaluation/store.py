"""Structured local records (JSON Lines under ``.susanoo/records/``)."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo.paths import home_state, is_initialized, records_dir, sibling_checkouts

RECORD_KINDS = ("routes", "outcomes", "evidence", "research", "approvals", "enforcement")


class RecordError(Exception):
    pass


def new_record_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"su-{stamp}-{secrets.token_hex(2)}"


class RecordStore:
    """Append-only JSONL files, one per record kind. Corrupt lines are skipped and counted."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = records_dir(root)
        self.skipped_lines = 0

    def _note_global_project(self) -> None:
        """In global mode, remember which project a records folder belongs to."""
        meta = self.directory.parent / "project.json"
        if self.directory.parent.parent == home_state() / "projects" and not meta.exists():
            meta.write_text(json.dumps({"path": str(self.root.resolve())}) + "\n", encoding="utf-8")

    def path(self, kind: str) -> Path:
        if kind not in RECORD_KINDS:
            raise RecordError(f"unknown record kind '{kind}'")
        return self.directory / f"{kind}.jsonl"

    def append(self, kind: str, data: dict[str, Any]) -> None:
        path = self.path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._note_global_project()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n")

    def read(self, kind: str) -> list[dict[str, Any]]:
        path = self.path(kind)
        if not path.is_file():
            return []
        items: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    self.skipped_lines += 1
                    continue
                if isinstance(value, dict):
                    items.append(value)
                else:
                    self.skipped_lines += 1
        return items

    def resolve_route(self, ref: str, session_id: str | None = None) -> dict[str, Any]:
        """Find a route record by full id, unique prefix, or ``latest``.

        With ``session_id``, ``latest`` means the newest route created in that session
        (falling back to the newest overall), so concurrent sessions do not act on each
        other's routes.
        """
        routes = self.read("routes")
        if not routes:
            raise RecordError(
                "no route records yet (records are written by `susanoo route` after `susanoo init`)"
            )
        if ref == "latest":
            own = _own(routes, session_id)
            return own[-1] if own else routes[-1]
        matches = _matching(routes, ref)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RecordError(f"no route record matches '{ref}'")
        raise RecordError(f"'{ref}' matches {len(matches)} records; use a longer prefix")


def _own(routes: list[dict[str, Any]], session_id: str | None) -> list[dict[str, Any]]:
    return [r for r in routes if session_id and r.get("session_id") == session_id]


def _matching(routes: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    exact = [r for r in routes if r.get("id") == ref]
    return exact[-1:] if exact else [r for r in routes if str(r.get("id", "")).startswith(ref)]


def find_route(root: Path, ref: str, session_id: str | None = None) -> tuple[RecordStore, dict[str, Any]]:
    """Resolve a route in ``root``'s records or in another checkout of its repository.

    DEEP work happens in a git worktree while the route was often recorded in the main checkout
    (or the other way round), and each checkout has its own records. ``latest`` is the current
    session's newest own route in any checkout (this one wins a tie), so another session's route
    is never taken from elsewhere. A route id or prefix that matches nothing here is looked up in
    the repository's other checkouts. Returns the store that holds the route: evidence and
    outcomes belong next to it.
    """
    store = RecordStore(root)
    routes = store.read("routes")
    if ref == "latest" and session_id:
        hits = [(store, route) for route in _own(routes, session_id)[-1:]]
        for other in sibling_stores(root):
            hits += [(other, route) for route in _own(other.read("routes"), session_id)[-1:]]
        if hits:  # `max` keeps the first of equals: this checkout's route on a tie
            return max(hits, key=lambda hit: str(hit[1].get("created_at", "")))
    if ref == "latest" or _matching(routes, ref):
        return store, _resolve_here(store, ref, session_id)
    hits = []
    for other in sibling_stores(root):
        hits += [(other, route) for route in _matching(other.read("routes"), ref)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        places = ", ".join(sorted({str(hit[0].root) for hit in hits}))
        raise RecordError(f"'{ref}' matches {len(hits)} records in {places}; use a longer prefix")
    return store, _resolve_here(store, ref, session_id)


def sibling_stores(root: Path) -> list[RecordStore]:
    """The record stores of the repository's other checkouts where Susanoo is active."""
    return [RecordStore(checkout) for checkout in sibling_checkouts(root) if is_initialized(checkout)]


def _resolve_here(store: RecordStore, ref: str, session_id: str | None) -> dict[str, Any]:
    """``store.resolve_route``, naming the repository's other checkouts when this one has no routes."""
    try:
        return store.resolve_route(ref, session_id)
    except RecordError as exc:
        if store.read("routes"):
            raise
        others = [str(other.root) for other in sibling_stores(store.root) if other.read("routes")]
        if not others:
            raise
        raise RecordError(
            f"this checkout has no route records; other checkouts of this repository do "
            f"({', '.join(others)}): pass the route id, or run the command there"
        ) from exc
