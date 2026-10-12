"""Capability inventory data types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    """How confident DevRoute is that a capability can actually be used.

    AVAILABLE_VERIFIED       a functional check passed (``--verify`` version probe, a
                             parsed Claude Code definition, or a running Claude Code session)
    INSTALLED_UNVERIFIED     present (on PATH, installed in the project) but not exercised
    CONFIGURED_INACCESSIBLE  declared or configured, but not usable from here
                             (dependency not installed, MCP command missing, broken definition)
    MISSING                  required by the task and not found
    NOT_REQUIRED             not found and not needed for the task
    """

    AVAILABLE_VERIFIED = "available_verified"
    INSTALLED_UNVERIFIED = "installed_unverified"
    CONFIGURED_INACCESSIBLE = "configured_inaccessible"
    MISSING = "missing"
    NOT_REQUIRED = "not_required"

    @property
    def usable(self) -> bool:
        return self in (Status.AVAILABLE_VERIFIED, Status.INSTALLED_UNVERIFIED)

    @property
    def rank(self) -> int:
        order = {
            Status.AVAILABLE_VERIFIED: 4,
            Status.INSTALLED_UNVERIFIED: 3,
            Status.CONFIGURED_INACCESSIBLE: 2,
            Status.MISSING: 1,
            Status.NOT_REQUIRED: 0,
        }
        return order[self]


@dataclass
class Finding:
    """One discovered (or configured) thing in the environment."""

    id: str
    kind: str  # tool | skill | agent | command | mcp_server | plugin | language | build_system | vcs
    name: str
    status: Status
    capabilities: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    scope: str = "project"  # project | user | system
    tool_kind: str | None = None  # registry kind for tools (cli, mcp, native, ...)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class Inventory:
    root: str
    generated_at: str
    verified: bool
    languages: dict[str, int]
    findings: list[Finding]
    warnings: list[str] = field(default_factory=list)
    source_files: int = 0

    def by_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def get(self, finding_id: str) -> Finding | None:
        for finding in self.findings:
            if finding.id == finding_id:
                return finding
        return None

    def for_capability(self, capability: str) -> list[Finding]:
        found = [f for f in self.findings if capability in f.capabilities]
        return sorted(found, key=lambda f: f.status.rank, reverse=True)

    def capability_status(self, capability: str) -> Status | None:
        found = self.for_capability(capability)
        return found[0].status if found else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "verified": self.verified,
            "languages": self.languages,
            "source_files": self.source_files,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
        }
