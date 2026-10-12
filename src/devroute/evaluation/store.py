"""Structured local records (JSON Lines under ``.devroute/records/``)."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devroute.paths import records_dir

RECORD_KINDS = ("routes", "outcomes", "evidence", "research", "approvals", "enforcement")


class RecordError(Exception):
    pass


def new_record_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"dr-{stamp}-{secrets.token_hex(2)}"


class RecordStore:
    """Append-only JSONL files, one per record kind. Corrupt lines are skipped and counted."""

    def __init__(self, root: Path) -> None:
        self.directory = records_dir(root)
        self.skipped_lines = 0

    def path(self, kind: str) -> Path:
        if kind not in RECORD_KINDS:
            raise RecordError(f"unknown record kind '{kind}'")
        return self.directory / f"{kind}.jsonl"

    def append(self, kind: str, data: dict[str, Any]) -> None:
        path = self.path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
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
                "no route records yet (records are written by `devroute route` after `devroute init`)"
            )
        if ref == "latest":
            own = [r for r in routes if session_id and r.get("session_id") == session_id]
            return own[-1] if own else routes[-1]
        exact = [r for r in routes if r.get("id") == ref]
        if exact:
            return exact[-1]
        matches = [r for r in routes if str(r.get("id", "")).startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RecordError(f"no route record matches '{ref}'")
        raise RecordError(f"'{ref}' matches {len(matches)} records; use a longer prefix")
