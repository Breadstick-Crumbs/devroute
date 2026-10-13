"""Human-readable rendering. Output is plain ASCII Markdown so it reads well in terminals and in Claude Code."""

from __future__ import annotations

import json
from typing import Any

from susanoo.analyzer import Assessment
from susanoo.discovery.models import Inventory, Status
from susanoo.evaluation.report import Report
from susanoo.evaluation.suite import SuiteResult
from susanoo.pipeline import TaskPlan
from susanoo.research import ResearchReport
from susanoo.verification import Evidence

# Phases where the selected code-navigation tool is named in the brief.
NAVIGATION_PHASES = frozenset({"locate", "investigate", "requirements"})

STATUS_LABELS = {
    Status.AVAILABLE_VERIFIED: "verified",
    Status.INSTALLED_UNVERIFIED: "installed (unverified)",
    Status.CONFIGURED_INACCESSIBLE: "configured, not usable",
    Status.MISSING: "missing",
    Status.NOT_REQUIRED: "not required",
}


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False, default=str)


def _truncate(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_assessment(a: Assessment) -> str:
    lines = [
        "## Assessment",
        "",
        "Heuristic estimate from the task text and project files; not a validated measurement.",
        "",
        f"- Complexity:  {a.complexity.level.upper()} ({a.complexity.score:.2f})"
        + (f" - {', '.join(a.complexity.drivers)}" if a.complexity.drivers else ""),
        f"- Risk:        {a.risk.level.upper()} ({a.risk.score:.2f})"
        + (f" - {', '.join(a.risk.drivers)}" if a.risk.drivers else ""),
        f"- Uncertainty: {a.uncertainty.level.upper()} ({a.uncertainty.score:.2f})"
        + (f" - {', '.join(a.uncertainty.drivers)}" if a.uncertainty.drivers else ""),
        f"- Confidence:  {a.confidence}",
    ]
    for note in a.confidence_notes:
        lines.append(f"  - {note}")
    if a.areas:
        lines.append(f"- Feature areas: {', '.join(a.areas)}")
    lines += ["", "| Dimension | Level | Why |", "|---|---|---|"]
    for dim in a.dimensions.values():
        why = "; ".join(r for r in dim.reasons if not r.startswith("baseline")) or "-"
        lines.append(f"| {dim.name} | {dim.level} {dim.label} | {_truncate(why, 140)} |")
    if a.context_notes:
        lines += ["", "Project context:"] + [f"- {n}" for n in a.context_notes]
    return "\n".join(lines)


def render_brief(plan: TaskPlan) -> str:
    a, d, p = plan.assessment, plan.decision, plan.profile
    lines = ["# Susanoo execution brief", ""]
    if plan.record_id:
        lines += [f"Record: `{plan.record_id}` (stored locally in .susanoo/records)", ""]

    lines += ["## Task", "", _truncate(a.task, 600), ""]
    signals = ", ".join(s.label for s in a.signals) or "none recognized"
    lines += [f"Detected signals: {signals}", ""]

    lines += [render_assessment(a), ""]

    lines += [f"## Recommended mode: {p.title}", "", p.summary, ""]
    lines += ["Why: " + "; ".join(d.reasons), ""]
    for note in d.override_notes:
        lines.append(f"> {note}")
    if d.override_notes:
        lines.append("")
    lines += [
        f"- Planning: {p.planning}",
        f"- Agents: {p.delegation}",
        f"- Review: {p.review}",
        f"- Isolation: {p.isolation}",
        f"- Verification depth: {p.verification_depth}",
        "",
    ]

    if d.controls:
        lines += ["## Mandatory safety controls", "", "These apply regardless of the selected profile.", ""]
        for c in d.controls:
            lines.append(f"- **{c.id}** ({c.reason}): {c.description}")
        lines.append("")

    lines += ["## Capabilities", ""]
    available = [r for r in plan.recommendations if not r.gap]
    if available:
        lines += ["Available tools:", ""]
        for r in available:
            if r.selected is None:
                continue
            cmd = f" - `{' '.join(r.selected.command)}`" if r.selected.command else ""
            lines.append(
                f"- {r.title} [{r.level}]: {r.selected.name} ({r.selected.kind}, "
                f"{STATUS_LABELS[r.selected.status]}){cmd}"
            )
        lines.append("")
    if plan.gaps:
        lines += ["Missing capabilities:", ""]
        for g in plan.gaps:
            impact = "blocking until resolved or explicitly accepted" if g.blocking else "non-blocking"
            lines.append(f"- {g.title} [{g.level}, {impact}]: {g.suggestion}")
        lines += ["", "Susanoo never installs tools itself; adopting any of these needs your approval.", ""]
    else:
        lines += ["Missing capabilities: none for this task.", ""]
    useful = [f for f in plan.inventory.findings if f.kind in ("skill", "agent") and f.status.usable]
    if useful:
        names = ", ".join(f"{f.name} ({f.kind})" for f in useful[:8])
        lines += [f"Claude Code skills and subagents available: {names}", ""]

    lines += ["## Proposed steps", ""]
    nav = next(
        (r.selected for r in plan.recommendations if r.capability == "code_navigation" and r.selected), None
    )
    for i, phase in enumerate(p.phases, 1):
        marker = " **[approval]**" if phase.approval else ""
        hint = f" Navigation: {nav.name}." if nav and phase.id in NAVIGATION_PHASES else ""
        lines.append(f"{i}. **{phase.title}**{marker} - {phase.action}{hint}")
    lines.append("")

    lines += ["## Required verification", ""]
    if not plan.verification.steps:
        lines.append("- No automated verification selected; review the change manually.")
    for s in plan.verification.steps:
        if s.mode == "ready" and s.command:
            how = f"`{' '.join(s.command)}`"
        elif s.mode == "manual":
            how = f"{s.tool} (performed by the agent or a person)"
        else:
            how = "no usable tool - gap"
        lines.append(f"- [{s.level}] {s.title}: {how}")
    lines += [
        "",
        "Only checks that actually ran count as evidence. Run `susanoo verify --execute` "
        "to execute and record the ready checks.",
        "",
    ]

    lines += ["## Approval checkpoints", ""]
    if d.checkpoints:
        lines += [f"- {c}" for c in d.checkpoints]
    else:
        lines.append("- None required by Susanoo for this task. Normal judgment still applies.")
    lines.append("")

    lines += [
        "## Limitations",
        "",
        "- The assessment uses configurable keyword heuristics and local project files; it can miss context "
        "that is not in the task description.",
        "- Tool statuses come from read-only detection"
        + (
            " plus version checks."
            if plan.inventory.verified
            else "; run `susanoo inventory --verify` to check versions."
        ),
    ]
    return "\n".join(lines) + "\n"


def render_inventory(inv: Inventory) -> str:
    lines = [f"# Capability inventory: {inv.root}", ""]
    if inv.languages:
        langs = ", ".join(f"{k} ({v})" for k, v in inv.languages.items())
        lines += [f"Languages: {langs}", ""]
    groups = [
        ("Build systems", "build_system"),
        ("Version control", "vcs"),
        ("Tools", "tool"),
        ("Claude Code skills", "skill"),
        ("Claude Code commands", "command"),
        ("Claude Code subagents", "agent"),
        ("MCP servers", "mcp_server"),
        ("Plugins", "plugin"),
    ]
    for title, kind in groups:
        items = inv.by_kind(kind)
        lines.append(f"## {title}")
        if not items:
            lines += ["", "- none found", ""]
            continue
        lines.append("")
        for f in sorted(items, key=lambda f: (-f.status.rank, f.name)):
            caps = f" -> {', '.join(f.capabilities)}" if f.capabilities else ""
            scope = f" [{f.scope}]" if kind in ("skill", "command", "agent", "mcp_server", "plugin") else ""
            lines.append(f"- {f.name}{scope}: {STATUS_LABELS[f.status]}{caps}")
            if f.evidence:
                lines.append(f"    {_truncate('; '.join(f.evidence), 150)}")
        lines.append("")
    if inv.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in inv.warnings] + [""]
    if not inv.verified:
        lines.append("Detection was read-only. `susanoo inventory --verify` also runs `--version` checks.")
    return "\n".join(lines) + "\n"


def render_research(report: ResearchReport) -> str:
    lines = [f"# Capability research: {report.title} ({report.capability})", ""]
    lines += [f"Decision: **{report.decision}**", "", report.recommendation, "", "## Candidates", ""]
    if not report.candidates:
        lines.append("- none in the local registry")
    for c in report.candidates:
        lines.append(f"- {c.name} ({c.kind}): {STATUS_LABELS[c.status]}")
        if c.install and not c.status.usable:
            lines.append(f"    install: {c.install}")
        for note in c.security_notes:
            lines.append(f"    security: {note}")
        if c.notes:
            lines.append(f"    note: {_truncate(c.notes)}")
    lines += ["", "## Validation steps", ""] + [f"{i}. {s}" for i, s in enumerate(report.validation_steps, 1)]
    lines += ["", "## Limitations", ""] + [f"- {s}" for s in report.limitations]
    return "\n".join(lines) + "\n"


def render_evidence(items: list[Evidence]) -> str:
    if not items:
        return "No verification steps matched.\n"
    lines = ["| Step | Result | Source | Command | Exit | Seconds |", "|---|---|---|---|---|---|"]
    for e in items:
        cmd = " ".join(e.command) if e.command else "-"
        code = "-" if e.exit_code is None else str(e.exit_code)
        secs = "-" if e.duration_s is None else f"{e.duration_s:.1f}"
        lines.append(f"| {e.step} | {e.status} | {e.source} | `{_truncate(cmd, 60)}` | {code} | {secs} |")
    notes = [f"- {e.step}: {e.note}" for e in items if e.note]
    failures = [e for e in items if e.status in ("failed", "error", "timeout") and e.output_tail]
    out = "\n".join(lines)
    if notes:
        out += "\n\n" + "\n".join(notes)
    for e in failures:
        out += f"\n\n### {e.step} output (tail)\n\n```\n{e.output_tail.strip()[-1200:]}\n```"
    return out + "\n"


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.0%}" if suffix == "%" else f"{value:g}{suffix}"
    return f"{value}{suffix}"


def render_report(
    routes: list[dict[str, Any]], report: Report, outcomes: dict[str, dict[str, Any]], last: int
) -> str:
    lines = ["# Susanoo records", ""]
    if not routes:
        return "No records yet. Records are written by `susanoo route` once `susanoo init` has run.\n"
    lines += [
        "## Recent routes",
        "",
        "| Record | Profile | Risk | Complexity | Outcome | Task |",
        "|---|---|---|---|---|---|",
    ]
    for r in routes[-last:][::-1]:
        outcome = outcomes.get(str(r.get("id")), {}).get("status", "-")
        profile = str(r.get("profile", "?")).upper() + (" (override)" if r.get("overridden") else "")
        task = r.get("task") or "(task text not stored)"
        lines.append(
            f"| {r.get('id')} | {profile} | {r.get('risk', {}).get('level', '?')} | "
            f"{r.get('complexity', {}).get('level', '?')} | {outcome} | {_truncate(str(task), 60)} |"
        )
    lines += [
        "",
        "## By profile",
        "",
        "| Profile | Routes | Outcomes | Completed | Mean min | Mean interventions | Checks run | Check failures |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in report.profiles:
        lines.append(
            f"| {s.profile.upper()} | {s.routes} | {s.with_outcome} | {_fmt(s.completion_rate, '%')} | "
            f"{_fmt(s.mean_minutes)} | {_fmt(s.mean_interventions)} | {s.executed_checks} | {_fmt(s.check_failure_rate, '%')} |"
        )
    lines += ["", "## Recommendations for review", ""]
    if report.insights:
        for i in report.insights:
            lines.append(f"- {i.profile.upper()} (n={i.sample_size}): {i.observation} {i.suggestion}")
        lines.append("\nThese are suggestions only. Susanoo does not change configuration automatically.")
    else:
        lines.append("- None.")
    if report.notes:
        lines += [""] + [f"- {n}" for n in report.notes]
    return "\n".join(lines) + "\n"


def render_suite(result: SuiteResult) -> str:
    lines = [
        f"# Routing evaluation: {result.suite}",
        "",
        f"Cases: {result.total}",
        f"Profile agreement: {result.profile_agreement:.1%}",
        f"Risk-floor violations: {result.risk_floor_violations}",
        "",
        "Labels are maintainer expectations, not ground truth. This checks routing behavior, not outcomes.",
        "",
        "| expected \\ routed | FAST | STANDARD | DEEP |",
        "|---|---|---|---|",
    ]
    for expected, row in result.confusion.items():
        lines.append(f"| {expected.upper()} | {row['fast']} | {row['standard']} | {row['deep']} |")
    if result.failures:
        lines += ["", "## Mismatches", ""]
        for c in result.failures:
            lines.append(f'- {c.id}: {"; ".join(c.problems)} - "{_truncate(c.task, 90)}"')
    return "\n".join(lines) + "\n"
