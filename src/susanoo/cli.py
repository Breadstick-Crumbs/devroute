"""Susanoo command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from susanoo import __version__
from susanoo.adapters.claude_code.installer import (
    actions_to_dicts,
    apply_install,
    apply_uninstall,
    enforcement_enabled,
    plan_install,
    plan_uninstall,
    sha256,
    skill_template,
)
from susanoo.adapters.claude_code.legacy import (
    MigrationError,
    legacy_present,
    migrate_state,
    migrated_manifest,
    plan_legacy_files,
    plan_migration,
    planned_enforce,
    remove_legacy_files,
    stale_hook_settings,
    state_sources,
)
from susanoo.analyzer import assess
from susanoo.config import PROFILE_ORDER, Config, ConfigError, load_config
from susanoo.discovery import build_inventory
from susanoo.enforcement import HOOK_EVENTS, evidence_status, handle
from susanoo.evaluation import (
    OUTCOME_STATUSES,
    RecordError,
    RecordStore,
    new_record_id,
    run_suite,
    summarize,
)
from susanoo.evaluation.report import latest_by_record
from susanoo.paths import find_project_root, is_initialized, state_dir
from susanoo.pipeline import TaskPlan, plan_task, project_context, route_record
from susanoo.registry import load_registry
from susanoo.render import (
    render_assessment,
    render_brief,
    render_evidence,
    render_inventory,
    render_report,
    render_research,
    render_suite,
    to_json,
)
from susanoo.research import research
from susanoo.router import load_profiles
from susanoo.verification import EVIDENCE_STATUSES, execute_plan, reported_evidence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class CLIError(Exception):
    """A user-facing error: printed without a traceback."""


@dataclass
class Context:
    root: Path
    args: argparse.Namespace
    out: TextIO
    err: TextIO

    _config: Config | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            extra = Path(self.args.config) if getattr(self.args, "config", None) else None
            self._config = load_config(self.root, extra)
        return self._config

    def emit(self, text: str) -> None:
        self.out.write(text if text.endswith("\n") else text + "\n")

    def emit_json(self, data: Any) -> None:
        self.emit(to_json(data))


def _task_text(args: argparse.Namespace, stdin: TextIO) -> str:
    parts = list(getattr(args, "task", None) or [])
    if getattr(args, "stdin", False):
        if parts:
            raise CLIError("give the task either as an argument or with --stdin, not both")
        text = stdin.read()
    elif parts == ["-"]:
        text = stdin.read()
    else:
        text = " ".join(parts)
    text = " ".join(text.split())
    if not text:
        raise CLIError("no task description given (pass it as an argument, or use --stdin)")
    return text


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_assess(ctx: Context, stdin: TextIO) -> int:
    task = _task_text(ctx.args, stdin)
    registry = load_registry(ctx.root)
    inventory = build_inventory(ctx.root, registry, include_claude_code=False)
    assessment = assess(task, ctx.config, project_context(inventory))
    if ctx.args.json:
        ctx.emit_json(assessment.to_dict())
    else:
        ctx.emit(f"# Susanoo assessment\n\nTask: {assessment.task}\n\n" + render_assessment(assessment))
    return EXIT_OK


def _record_route(ctx: Context, plan: TaskPlan) -> str | None:
    if ctx.args.no_record or not is_initialized(ctx.root):
        return None
    record_id = new_record_id()
    session = ctx.args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    RecordStore(ctx.root).append("routes", route_record(plan, record_id, ctx.config, session))
    return record_id


def cmd_route(ctx: Context, stdin: TextIO) -> int:
    task = _task_text(ctx.args, stdin)
    plan = plan_task(
        task, ctx.root, ctx.config, override=ctx.args.profile, verify_tools=ctx.args.verify_tools
    )
    plan.record_id = _record_route(ctx, plan)
    if ctx.args.json:
        ctx.emit_json(plan.to_dict(include_inventory=ctx.args.include_inventory))
    else:
        ctx.emit(render_brief(plan))
        if plan.record_id is None and not ctx.args.no_record:
            ctx.err.write("note: not recorded (run `susanoo init` to enable local records)\n")
    return EXIT_OK


def cmd_inventory(ctx: Context, stdin: TextIO) -> int:
    inventory = build_inventory(ctx.root, load_registry(ctx.root), verify=ctx.args.verify)
    if ctx.args.json:
        ctx.emit_json(inventory.to_dict())
    else:
        ctx.emit(render_inventory(inventory))
    return EXIT_OK


def cmd_init(ctx: Context, stdin: TextIO) -> int:
    args = ctx.args
    notes: list[str] = []
    if args.uninstall:
        own = plan_uninstall(ctx.root)
        legacy = plan_legacy_files(ctx.root)
        actions = own + legacy
        if not args.dry_run:
            apply_uninstall(ctx.root, own)
            remove_legacy_files(ctx.root, legacy)
    else:
        # A DevRoute installation is migrated in two phases: its state first (so records and the
        # enforce choice carry over), its old skill and subagents only after the new ones exist.
        migration = plan_migration(ctx.root)
        moving = any(a.action == "migrate" for a in migration)
        enforce = (
            True
            if args.enforce
            else False
            if args.no_enforce
            else planned_enforce(ctx.root, migration, enforcement_enabled(ctx.root))
        )
        if enforce and not shutil.which("susanoo"):
            ctx.err.write(
                "warning: enforcement hooks run `susanoo hook ...`, but `susanoo` is not on PATH. "
                "Hooks will fail open (Claude Code shows a hook error) until it is.\n"
            )
        if args.dry_run:
            # Preview the install as it will look once .devroute/ has moved.
            install = plan_install(
                ctx.root,
                force=args.force,
                with_agents=not args.no_agents,
                enforce=enforce,
                manifest_files=migrated_manifest(ctx.root)["files"] if moving else None,
                read_from=state_sources(ctx.root) if moving else None,
            )
        else:
            try:
                note = migrate_state(ctx.root, migration)
            except MigrationError as exc:
                raise CLIError(str(exc)) from exc
            if note:
                notes.append(note)
            install = plan_install(
                ctx.root, force=args.force, with_agents=not args.no_agents, enforce=enforce
            )
            apply_install(ctx.root, install, with_agents=not args.no_agents, enforce=enforce)
            remove_legacy_files(ctx.root, migration)
        actions = migration + install
    if args.json:
        ctx.emit_json({"root": str(ctx.root), "dry_run": args.dry_run, "actions": actions_to_dicts(actions)})
        return EXIT_OK
    title = "uninstall" if args.uninstall else "install"
    lines = [f"Susanoo {title} in {ctx.root}" + (" (dry run)" if args.dry_run else ""), ""]
    for a in actions:
        note = f"  ({a.note})" if a.note else ""
        lines.append(f"  {a.action:<14} {a.path}{note}")
    if not actions:
        lines.append("  nothing to do")
    lines.append("")
    conflicts = [a for a in actions if a.action == "conflict"]
    if conflicts:
        lines.append(f"{len(conflicts)} file(s) left untouched because they differ from Susanoo's version.")
    if any(a.action in ("migrate", "merge", "remove") for a in actions) and not args.uninstall:
        verb = "Will migrate" if args.dry_run else "Migrated"
        lines.append(f"{verb} the DevRoute installation; DevRoute is now Susanoo.")
    lines += notes
    if args.uninstall:
        if state_dir(ctx.root).is_dir():
            lines.append(
                "Local records in .susanoo/records were kept; delete them yourself if you no longer need them."
            )
    elif not args.dry_run:
        lines += [
            "Settings, CLAUDE.md, and MCP configuration were not modified.",
            "Next: run `susanoo doctor`, then in Claude Code: /susanoo <task description>",
        ]
    else:
        lines.append("No files were written (dry run). Re-run without --dry-run to apply.")
    ctx.emit("\n".join(lines))
    return EXIT_OK


def cmd_doctor(ctx: Context, stdin: TextIO) -> int:
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    py = sys.version_info
    check("python", "ok" if py >= (3, 11) else "fail", f"Python {py.major}.{py.minor}.{py.micro}")
    check("susanoo", "ok", f"version {__version__} at {Path(__file__).parent}")
    try:
        config = ctx.config
        load_profiles(ctx.root)
        registry = load_registry(ctx.root)
        check(
            "configuration",
            "ok",
            f"loaded from: {', '.join(config['_sources'])}; {len(registry.tools)} tools in registry",
        )
    except ConfigError as exc:
        check("configuration", "fail", str(exc))
    check("project root", "ok", str(ctx.root))
    check(
        "susanoo on PATH",
        "ok" if shutil.which("susanoo") else "warn",
        "found"
        if shutil.which("susanoo")
        else "not found; the /susanoo skill will fall back to `python -m susanoo`",
    )
    check(
        "git",
        "ok" if shutil.which("git") else "warn",
        "found" if shutil.which("git") else "git not found on PATH",
    )
    check(
        "git repository",
        "ok" if (ctx.root / ".git").exists() else "warn",
        "yes"
        if (ctx.root / ".git").exists()
        else "project is not a git repository; changes are harder to review and revert",
    )
    claude = shutil.which("claude")
    check(
        "claude code",
        "ok" if claude else "warn",
        "Claude Code CLI found"
        if claude
        else "Claude Code CLI not found on PATH (needed for /susanoo; the CLI works without it)",
    )

    if legacy_present(ctx.root):
        check(
            "DevRoute installation",
            "warn",
            "found .devroute/ or the old /devroute skill; run `susanoo init` to migrate it",
        )
    for settings in stale_hook_settings(ctx.root, Path.home()):
        check(
            "DevRoute hook in settings",
            "warn",
            f"{settings} still runs `devroute hook`; change it to `susanoo hook`",
        )
    skill = ctx.root / ".claude" / "skills" / "susanoo" / "SKILL.md"
    if not skill.is_file():
        check("/susanoo skill", "warn", "not installed; run `susanoo init`")
    elif (current := sha256(skill.read_text(encoding="utf-8"))) in (
        sha256(skill_template(False)),
        sha256(skill_template(True)),
    ):
        enforced = current == sha256(skill_template(True))
        check(
            "/susanoo skill", "ok", "installed and current" + (" (enforcement hooks on)" if enforced else "")
        )
        if enforced and not shutil.which("susanoo"):
            check(
                "enforcement",
                "warn",
                "hooks call `susanoo`, which is not on PATH; they will fail open until it is",
            )
    else:
        check(
            "/susanoo skill",
            "warn",
            "installed but differs from this Susanoo version (modified or outdated); `susanoo init` shows options",
        )
    agents = [
        n
        for n in ("susanoo-reviewer", "susanoo-researcher")
        if (ctx.root / ".claude" / "agents" / f"{n}.md").is_file()
    ]
    check(
        "subagents",
        "ok" if len(agents) == 2 else "warn",
        ", ".join(agents) if agents else "not installed (optional)",
    )
    if is_initialized(ctx.root):
        ignored = (state_dir(ctx.root) / ".gitignore").is_file()
        check(
            "local records",
            "ok" if ignored else "warn",
            "enabled; .susanoo/records is git-ignored"
            if ignored
            else "enabled, but .susanoo/.gitignore is missing",
        )
    else:
        check("local records", "warn", "disabled until `susanoo init` creates .susanoo/")

    failed = any(c["status"] == "fail" for c in checks)
    if ctx.args.json:
        ctx.emit_json({"ok": not failed, "checks": checks})
    else:
        width = max(len(c["check"]) for c in checks)
        lines = ["Susanoo doctor", ""]
        for c in checks:
            lines.append(f"  [{c['status']:<4}] {c['check']:<{width}}  {c['detail']}")
        lines += [
            "",
            "Result: "
            + (
                "problems found"
                if failed
                else "ready" + (" (with warnings)" if any(c["status"] == "warn" for c in checks) else "")
            ),
        ]
        ctx.emit("\n".join(lines))
    return EXIT_FAIL if failed else EXIT_OK


def cmd_report(ctx: Context, stdin: TextIO) -> int:
    store = RecordStore(ctx.root)
    routes, outcomes, evidence = store.read("routes"), store.read("outcomes"), store.read("evidence")
    report = summarize(routes, outcomes, evidence, min_samples=ctx.args.min_samples)
    if ctx.args.json:
        data = report.to_dict()
        data["routes"] = routes[-ctx.args.last :]
        data["skipped_corrupt_lines"] = store.skipped_lines
        ctx.emit_json(data)
    else:
        ctx.emit(render_report(routes, report, latest_by_record(outcomes), ctx.args.last))
        if store.skipped_lines:
            ctx.err.write(f"warning: skipped {store.skipped_lines} unreadable record line(s)\n")
    return EXIT_OK


def cmd_verify(ctx: Context, stdin: TextIO) -> int:
    args = ctx.args
    record: dict[str, Any] | None = None
    if args.task or args.stdin:
        task = _task_text(args, stdin)
        override = args.profile
    else:
        try:
            record = RecordStore(ctx.root).resolve_route(args.record, _session())
        except RecordError as exc:
            raise CLIError(f'{exc}. Pass the task text instead: susanoo verify "<task>"') from exc
        if not record.get("task"):
            raise CLIError(
                "that record does not store the task text (records.store_task_text = false); pass the task instead"
            )
        task = str(record["task"])
        override = args.profile or str(record.get("profile"))
    plan = plan_task(task, ctx.root, ctx.config, override=override)

    if not args.execute:
        if args.json:
            ctx.emit_json({"record_id": record.get("id") if record else None, **plan.verification.to_dict()})
        else:
            lines = [f"Verification plan ({plan.decision.profile.upper()})", ""]
            for s in plan.verification.steps:
                how = " ".join(s.command) if s.command else s.mode
                lines.append(f"  [{s.level:<11}] {s.id:<18} {s.mode:<11} {how}")
            lines += ["", "Nothing was run. Add --execute to run the 'ready' steps and record evidence."]
            ctx.emit("\n".join(lines))
        return EXIT_OK

    only = set(args.step) if args.step else None
    results = execute_plan(
        plan.verification, ctx.root, timeout=args.timeout, only=only, required_only=args.required_only
    )
    record_id = record.get("id") if record else None
    if record_id and not args.no_record and is_initialized(ctx.root):
        store = RecordStore(ctx.root)
        for item in results:
            item.record_id = str(record_id)
            store.append("evidence", item.to_dict())
    if args.json:
        ctx.emit_json({"record_id": record_id, "evidence": [e.to_dict() for e in results]})
    else:
        ctx.emit(render_evidence(results))
    required = {s.id for s in plan.verification.steps if s.level == "required"}
    failed = [e for e in results if e.step in required and e.status in ("failed", "error", "timeout")]
    return EXIT_FAIL if failed else EXIT_OK


def _session() -> str | None:
    """The Claude Code session running this command, if any (scopes `latest`)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _require_initialized(ctx: Context) -> RecordStore:
    if not is_initialized(ctx.root):
        raise CLIError("local records are disabled; run `susanoo init` first")
    return RecordStore(ctx.root)


def cmd_evidence(ctx: Context, stdin: TextIO) -> int:
    store = _require_initialized(ctx)
    try:
        record = store.resolve_route(ctx.args.record, _session())
    except RecordError as exc:
        raise CLIError(str(exc)) from exc
    try:
        item = reported_evidence(
            ctx.args.step, ctx.args.status, ctx.args.evidence_command, ctx.args.note or ""
        )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    item.record_id = str(record["id"])
    store.append("evidence", item.to_dict())
    if ctx.args.json:
        ctx.emit_json(item.to_dict())
    else:
        ctx.emit(
            f"Recorded reported evidence for {item.record_id}: {item.step} = {item.status} (source: reported)"
        )
    return EXIT_OK


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def cmd_outcome(ctx: Context, stdin: TextIO) -> int:
    store = _require_initialized(ctx)
    try:
        record = store.resolve_route(ctx.args.record, _session())
    except RecordError as exc:
        raise CLIError(str(exc)) from exc
    a = ctx.args
    for name in ("minutes", "interventions", "tokens", "cost", "regressions"):
        value = getattr(a, name)
        if value is not None and value < 0:
            raise CLIError(f"--{name} must not be negative")
    missing, failing = evidence_status(record, store.read("evidence"))
    unmet = missing + failing
    if a.status == "completed" and a.source == "agent" and unmet and not a.accept_missing_evidence:
        raise CLIError(
            "cannot record 'completed': required verification lacks passing evidence: "
            + ", ".join(unmet)
            + ". Run `susanoo verify --execute`, record checks with `susanoo evidence`, "
            "or record --status partial."
        )
    data = {
        "record_id": record["id"],
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": a.status,
        "source": a.source,
        "duration_minutes": a.minutes,
        "human_interventions": a.interventions,
        "tokens": a.tokens,
        "cost_usd": a.cost,
        "regressions": a.regressions,
        "agents": _csv(a.agents),
        "tools": _csv(a.tools),
        "notes": a.notes or "",
        "unmet_verification": unmet,
    }
    store.append("outcomes", data)
    if a.json:
        ctx.emit_json(data)
    else:
        ctx.emit(f"Recorded outcome for {record['id']}: {a.status} (source: {a.source})")
    return EXIT_OK


def cmd_approve(ctx: Context, stdin: TextIO) -> int:
    store = _require_initialized(ctx)
    try:
        record = store.resolve_route(ctx.args.record, _session())
    except RecordError as exc:
        raise CLIError(str(exc)) from exc
    data = {
        "record_id": record["id"],
        "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checkpoint": ctx.args.checkpoint,
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "note": ctx.args.note or "",
    }
    store.append("approvals", data)
    if ctx.args.json:
        ctx.emit_json(data)
    else:
        ctx.emit(f"Recorded approval of '{ctx.args.checkpoint}' for {record['id']}.")
    return EXIT_OK


def cmd_hook(ctx: Context, stdin: TextIO) -> int:
    raw = stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise CLIError(f"hook input is not valid JSON: {exc.msg}") from exc
    if not isinstance(event, dict):
        raise CLIError("hook input must be a JSON object")
    code, output = handle(ctx.args.event, event)
    if output:
        ctx.emit(output)
    return code


def cmd_research(ctx: Context, stdin: TextIO) -> int:
    registry = load_registry(ctx.root)
    if ctx.args.list or not ctx.args.capability:
        rows = [f"  {c.id:<24} {c.title}" for c in registry.capabilities.values()]
        ctx.emit("Capabilities:\n" + "\n".join(rows) + "\n\nUsage: susanoo research <capability>")
        return EXIT_OK
    inventory = build_inventory(ctx.root, registry)
    try:
        report = research(ctx.args.capability, inventory, registry, task=ctx.args.task)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    if not ctx.args.no_record and is_initialized(ctx.root):
        RecordStore(ctx.root).append("research", report.to_dict())
    if ctx.args.json:
        ctx.emit_json(report.to_dict())
    else:
        ctx.emit(render_research(report))
    return EXIT_OK


def cmd_eval(ctx: Context, stdin: TextIO) -> int:
    suite = Path(ctx.args.suite) if ctx.args.suite else None
    result = run_suite(ctx.config, load_profiles(ctx.root), suite)
    if ctx.args.json:
        ctx.emit_json(result.to_dict())
    else:
        ctx.emit(render_suite(result))
    below = ctx.args.min_agreement is not None and result.profile_agreement < ctx.args.min_agreement
    if below:
        ctx.err.write(
            f"profile agreement {result.profile_agreement:.1%} is below --min-agreement {ctx.args.min_agreement:.1%}\n"
        )
    if ctx.args.fail_on_risk_violation and result.risk_floor_violations:
        ctx.err.write(f"{result.risk_floor_violations} risk-floor violation(s)\n")
        return EXIT_FAIL
    return EXIT_FAIL if below else EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", help="project directory (default: nearest ancestor with .git or .susanoo)")
    common.add_argument("--config", help="extra TOML config merged over project config")
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")

    parser = argparse.ArgumentParser(
        prog="susanoo",
        description="Susanoo: the right workflow for every development task.",
        epilog="Run `susanoo <command> --help` for command options.",
    )
    parser.add_argument("--version", action="version", version=f"susanoo {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def task_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("task", nargs="*", help="task description (or '-' / --stdin to read it from stdin)")
        p.add_argument("--stdin", action="store_true", help="read the task description from stdin")

    p = sub.add_parser("doctor", parents=[common], help="check installation and environment health")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("inventory", parents=[common], help="discover local engineering capabilities")
    p.add_argument("--verify", action="store_true", help="also run `--version` checks for found tools")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("assess", parents=[common], help="assess task complexity, risk, and uncertainty")
    task_args(p)
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser("route", parents=[common], help="recommend a workflow and print the execution brief")
    task_args(p)
    p.add_argument(
        "--profile",
        choices=PROFILE_ORDER,
        help="override the recommended profile (safety controls still apply)",
    )
    p.add_argument("--no-record", action="store_true", help="do not write a local record")
    p.add_argument("--verify-tools", action="store_true", help="run `--version` checks during discovery")
    p.add_argument(
        "--include-inventory", action="store_true", help="include the full inventory in --json output"
    )
    p.add_argument("--session", help="Claude Code session id (default: $CLAUDE_CODE_SESSION_ID)")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("init", parents=[common], help="install the /susanoo skill and project files safely")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    p.add_argument("--force", action="store_true", help="replace differing files (a backup is written first)")
    p.add_argument("--no-agents", action="store_true", help="do not install the susanoo-* subagents")
    p.add_argument(
        "--uninstall", action="store_true", help="remove files Susanoo installed (unmodified ones only)"
    )
    p.add_argument(
        "--enforce",
        action="store_true",
        help="add enforcement hooks to the /susanoo skill (plan-approval, evidence, irreversible-action gates)",
    )
    p.add_argument("--no-enforce", action="store_true", help="install the skill without enforcement hooks")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("report", parents=[common], help="show local records, summaries, and recommendations")
    p.add_argument("--last", type=int, default=10, help="number of recent routes to list (default 10)")
    p.add_argument("--min-samples", type=int, default=10, help="outcomes needed before suggesting changes")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("verify", parents=[common], help="show or execute the verification plan")
    task_args(p)
    p.add_argument("--record", default="latest", help="route record to verify (id, prefix, or 'latest')")
    p.add_argument("--profile", choices=PROFILE_ORDER, help="profile to plan verification for")
    p.add_argument("--execute", action="store_true", help="run the ready steps and record evidence")
    p.add_argument("--step", action="append", help="only this step id (repeatable)")
    p.add_argument("--required-only", action="store_true", help="only required steps")
    p.add_argument("--timeout", type=float, default=900, help="per-command timeout in seconds (default 900)")
    p.add_argument("--no-record", action="store_true", help="do not store evidence")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("evidence", parents=[common], help="record a verification result you ran yourself")
    p.add_argument("record", help="route record id, prefix, or 'latest'")
    p.add_argument("--step", required=True, help="verification step id, e.g. unit_tests")
    p.add_argument("--status", required=True, choices=EVIDENCE_STATUSES)
    # dest differs from "command", which names the selected subcommand.
    p.add_argument("--command", dest="evidence_command", help="the command that was run")
    p.add_argument("--note", help="short summary of the result")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("outcome", parents=[common], help="record how a routed task ended")
    p.add_argument("record", help="route record id, prefix, or 'latest'")
    p.add_argument("--status", required=True, choices=OUTCOME_STATUSES)
    p.add_argument(
        "--source", choices=("human", "agent"), default="human", help="who is reporting (default human)"
    )
    p.add_argument("--minutes", type=float, help="wall-clock duration in minutes")
    p.add_argument("--interventions", type=int, help="number of human interventions or corrections")
    p.add_argument("--tokens", type=int, help="total tokens consumed, if known")
    p.add_argument("--cost", type=float, help="total cost in USD, if known")
    p.add_argument("--regressions", type=int, help="regressions found after completion")
    p.add_argument("--agents", help="comma-separated agents/subagents used")
    p.add_argument("--tools", help="comma-separated main tools used")
    p.add_argument("--notes", help="free-text note")
    p.add_argument(
        "--accept-missing-evidence",
        action="store_true",
        help="allow an agent to record 'completed' although required checks lack passing evidence",
    )
    p.set_defaults(func=cmd_outcome)

    p = sub.add_parser("approve", parents=[common], help="record that you approved the plan for a route")
    p.add_argument("record", nargs="?", default="latest", help="route record id, prefix, or 'latest'")
    p.add_argument("--checkpoint", default="plan", help="which checkpoint is approved (default: plan)")
    p.add_argument("--note", help="optional note")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("hook", help="entry point for Claude Code hooks (reads the event JSON on stdin)")
    p.add_argument("event", choices=HOOK_EVENTS)
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser(
        "research", parents=[common], help="evaluate options for a capability gap (local registry)"
    )
    p.add_argument("capability", nargs="?", help="capability id, e.g. browser_testing")
    p.add_argument("--list", action="store_true", help="list known capability ids")
    p.add_argument("--task", help="task context to store with the research record")
    p.add_argument("--no-record", action="store_true", help="do not write a research record")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("eval", parents=[common], help="evaluate routing against a labeled task suite")
    p.add_argument("--suite", help="path to a suite TOML (default: bundled routing-v1)")
    p.add_argument("--min-agreement", type=float, help="exit 1 if profile agreement is below this (0-1)")
    p.add_argument("--fail-on-risk-violation", action="store_true", help="exit 1 on any risk-floor violation")
    p.set_defaults(func=cmd_eval)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    for stream in (out, err):
        reconfigure: Callable[..., Any] | None = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(out)
        return EXIT_USAGE

    start = Path(args.path) if getattr(args, "path", None) else None
    if start is not None and not start.is_dir():
        err.write(f"susanoo: error: --path {start} is not a directory\n")
        return EXIT_USAGE
    root = start.resolve() if start is not None else find_project_root()
    ctx = Context(root=root, args=args, out=out, err=err)
    try:
        result: int = args.func(ctx, stdin or sys.stdin)
        return result
    except (CLIError, ConfigError, RecordError, ValueError) as exc:
        err.write(f"susanoo: error: {exc}\n")
        return EXIT_FAIL
    except KeyboardInterrupt:
        err.write("susanoo: interrupted\n")
        return 130
    except Exception as exc:  # last resort: no tracebacks for users unless debugging
        if os.environ.get("SUSANOO_DEBUG"):
            raise
        err.write(
            f"susanoo: internal error: {type(exc).__name__}: {exc}\n"
            "Set SUSANOO_DEBUG=1 for a traceback, and please report it: "
            "https://github.com/Breadstick-Crumbs/susanoo/issues\n"
        )
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
