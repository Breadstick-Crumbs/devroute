from __future__ import annotations

from devroute.analyzer import CapabilityRequirement
from devroute.discovery import Finding, Inventory, Status
from devroute.research import analyze_gaps, research
from devroute.selection import recommend


def inventory(findings: list[Finding], languages: dict[str, int] | None = None) -> Inventory:
    return Inventory(
        root=".", generated_at="", verified=False, languages=languages or {"python": 5}, findings=findings
    )


def tool(tid: str, status: Status, caps: list[str], kind: str = "cli", scope: str = "project") -> Finding:
    return Finding(
        id=f"tool:{tid}", kind="tool", name=tid, status=status, capabilities=caps, scope=scope, tool_kind=kind
    )


def mcp(name: str, caps: list[str], status: Status = Status.INSTALLED_UNVERIFIED) -> Finding:
    return Finding(
        id=f"mcp:project:{name}",
        kind="mcp_server",
        name=name,
        status=status,
        capabilities=caps,
        tool_kind="mcp",
    )


def req(cap: str, level: str = "required") -> CapabilityRequirement:
    return CapabilityRequirement(cap, level, ["test"])


def test_cli_preferred_over_mcp_when_both_available(registry):
    inv = inventory(
        [
            tool("playwright", Status.INSTALLED_UNVERIFIED, ["browser_testing"]),
            mcp("playwright", ["browser_testing"]),
        ],
        {"typescript": 3},
    )
    [rec] = recommend([req("browser_testing")], inv, registry)
    assert rec.selected is not None and rec.selected.id == "playwright"
    assert "lower context overhead" in rec.rationale


def test_available_mcp_beats_missing_cli(registry):
    inv = inventory([mcp("playwright", ["browser_testing"])], {"typescript": 3})
    [rec] = recommend([req("browser_testing")], inv, registry)
    assert rec.selected is not None and rec.selected.kind == "mcp"


def test_missing_capability_is_a_gap_with_suggestion(registry):
    [rec] = recommend([req("payment_sandbox")], inventory([]), registry)
    assert rec.gap and rec.status is Status.MISSING
    assert rec.suggestion is not None and rec.suggestion.id == "stripe-cli"
    [gap] = analyze_gaps([rec])
    assert gap.blocking
    assert "Stripe CLI" in gap.suggestion


def test_recommended_gap_is_non_blocking(registry):
    [rec] = recommend([req("performance_testing", "recommended")], inventory([]), registry)
    assert not analyze_gaps([rec])[0].blocking


def test_language_specific_tools_are_not_suggested_for_other_languages(registry):
    inv = inventory(
        [tool("go-test", Status.INSTALLED_UNVERIFIED, ["unit_testing"], scope="system")], {"python": 4}
    )
    [rec] = recommend([req("unit_testing")], inv, registry)
    assert rec.selected is None
    assert rec.suggestion is not None and rec.suggestion.id == "pytest"


def test_project_scripts_are_only_candidates_when_present(registry):
    [rec] = recommend([req("unit_testing")], inventory([], {"javascript": 2}), registry)
    ids = {c.id for c in [rec.suggestion, *rec.alternatives] if c}
    assert "npm-test-script" not in ids


def test_ready_command_attached(registry):
    inv = inventory([tool("ruff", Status.INSTALLED_UNVERIFIED, ["static_analysis"])])
    [rec] = recommend([req("static_analysis")], inv, registry)
    assert rec.selected.command == ["ruff", "check", "."]


def test_research_decisions(registry):
    assert (
        research(
            "static_analysis",
            inventory([tool("ruff", Status.INSTALLED_UNVERIFIED, ["static_analysis"])]),
            registry,
        ).decision
        == "use-existing"
    )
    assert (
        research(
            "unit_testing",
            inventory([tool("pytest", Status.CONFIGURED_INACCESSIBLE, ["unit_testing"])]),
            registry,
        ).decision
        == "fix-configuration"
    )
    report = research("payment_sandbox", inventory([]), registry, task="billing")
    assert report.decision == "adopt"
    assert any(
        "MCP servers run with your user permissions" in n for c in report.candidates for n in c.security_notes
    )
    assert any("no internet search" in lim for lim in report.limitations)


def test_research_unknown_capability(registry):
    import pytest

    with pytest.raises(ValueError, match="unknown capability"):
        research("teleportation", inventory([]), registry)
