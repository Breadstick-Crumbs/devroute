from __future__ import annotations

from susanoo.analyzer import CapabilityRequirement
from susanoo.discovery import Finding, Inventory, Status
from susanoo.research import analyze_gaps, research
from susanoo.selection import SelectionContext, installed_extensions, recommend


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


# --- JavaScript package managers, task keywords, installed extensions ------------------------


def js_inventory(package_manager: str | None, findings: list[Finding] | None = None) -> Inventory:
    inv = inventory(findings or [], languages={"typescript": 5})
    inv.package_manager = package_manager
    return inv


def _pick(recs, capability):
    return next(r for r in recs if r.capability == capability)


def test_audit_and_install_hints_follow_the_package_manager(registry):
    for pm, audit, add in (
        ("pnpm", "pnpm audit", "pnpm add -D eslint"),
        ("bun", "bun audit", "bun add -d eslint"),
    ):
        recs = recommend([req("dependency_audit"), req("static_analysis")], js_inventory(pm), registry)
        audit_rec, lint_rec = _pick(recs, "dependency_audit"), _pick(recs, "static_analysis")
        names = [c.name for c in [audit_rec.suggestion, *audit_rec.alternatives] if c]
        assert audit in names and "npm audit" not in names  # npm audit needs package-lock.json
        assert lint_rec.suggestion is not None and lint_rec.suggestion.install == add


def test_package_script_runs_through_the_package_manager(registry):
    found = [tool("npm-test-script", Status.INSTALLED_UNVERIFIED, ["unit_testing"], kind="script")]
    for pm, argv in (
        ("pnpm", ["pnpm", "run", "test"]),
        ("bun", ["bun", "run", "test"]),
        (None, ["npm", "run", "test"]),
    ):
        rec = recommend([req("unit_testing")], js_inventory(pm, found), registry)[0]
        assert rec.selected is not None and rec.selected.command == argv


def test_configured_default_package_manager_without_a_lockfile(registry):
    context = SelectionContext(default_package_manager="pnpm")
    rec = recommend([req("static_analysis")], js_inventory(None), registry, context)[0]
    assert rec.suggestion is not None and rec.suggestion.install == "pnpm add -D eslint"
    # Without a lockfile no audit tool applies (each needs its manager's lockfile).
    audit = recommend([req("dependency_audit")], js_inventory(None), registry, context)[0]
    names = {c.name for c in [audit.selected, audit.suggestion, *audit.alternatives] if c}
    assert not names & {"npm audit", "pnpm audit", "bun audit"}


def test_run_commands_use_the_detected_manager_not_the_configured_default(registry):
    # Discovery checked for npm (no lockfile), so the ready check must run npm, not pnpm.
    found = [tool("npm-test-script", Status.INSTALLED_UNVERIFIED, ["unit_testing"], kind="script")]
    context = SelectionContext(default_package_manager="pnpm")
    rec = recommend([req("unit_testing")], js_inventory(None, found), registry, context)[0]
    assert rec.selected is not None and rec.selected.command == ["npm", "run", "test"]


def test_task_keywords_pick_the_matching_tool(registry):
    postgres = recommend(
        [req("database_tooling")],
        inventory([]),
        registry,
        SelectionContext(task="Store payment status in Postgres"),
    )[0]
    assert postgres.suggestion is not None and postgres.suggestion.name == "psql"
    assert "the task mentions 'postgres'" in postgres.suggestion.adjustments
    # Between two usable tools the keyword decides: psql (unverified) beats sqlite3 (verified).
    both = inventory(
        [
            tool("sqlite3", Status.AVAILABLE_VERIFIED, ["database_tooling"]),
            tool("psql", Status.INSTALLED_UNVERIFIED, ["database_tooling"]),
        ]
    )
    plain = recommend([req("database_tooling")], both, registry)[0]
    keyed = recommend(
        [req("database_tooling")], both, registry, SelectionContext(task="add a postgres index")
    )[0]
    assert plain.selected is not None and plain.selected.name == "sqlite3"
    assert keyed.selected is not None and keyed.selected.name == "psql"


def _discovered(kind: str, name: str, caps: list[str]) -> Finding:
    return Finding(
        id=f"{kind}:user:{name}",
        kind=kind,
        name=name,
        status=Status.AVAILABLE_VERIFIED,
        capabilities=caps,
        scope="user",
    )


def test_installed_extensions_name_one_of_each_kind_first(registry):
    found = [
        _discovered("agent", "graph-a", ["code_navigation"]),
        _discovered("agent", "graph-b", ["code_navigation"]),
        _discovered("agent", "graph-c", ["code_navigation"]),
        _discovered("skill", "graph", ["code_navigation"]),
        mcp("graph-mcp", ["code_navigation"]),
        Finding(
            id="tool:claude-code-search",
            kind="tool",
            name="Claude Code Grep / Glob / Read",
            status=Status.AVAILABLE_VERIFIED,
            capabilities=["code_navigation"],
            tool_kind="native",
        ),
    ]
    rec = recommend([req("code_navigation", "recommended")], inventory(found), registry)[0]
    assert rec.selected is not None and rec.selected.kind == "native"
    kinds = [c.kind for c in installed_extensions(rec)]
    assert sorted(kinds) == ["agent", "mcp", "skill"]


def test_gap_suggestion_includes_the_install_hint(registry):
    gaps = analyze_gaps(recommend([req("secret_scanning")], inventory([]), registry))
    assert gaps and "install: https://github.com/gitleaks" in gaps[0].suggestion
