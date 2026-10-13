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
from susanoo.adapters.claude_code.global_install import (
    apply_global,
    apply_global_uninstall,
    claude_home,
    plan_global,
    plan_global_uninstall,
    user_command,
)
from susanoo.adapters.claude_code.installer import (
    UNREADABLE,
    FileAction,
    _read,
    actions_to_dicts,
    apply_install,
    apply_uninstall,
    enforcement_enabled,
    enforcement_scope,
    plan_install,
    plan_uninstall,
    read_manifest,
    sha256,
    skill_template,
    update_manifest,
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
from susanoo.adapters.claude_code.settings_hooks import (
    GATE_EVENTS,
    SETTINGS_FILE,
    apply_settings,
    owned_events_in,
    plan_settings,
    settings_hooks,
)
from susanoo.analyzer import assess
from susanoo.config import PROFILE_ORDER, Config, ConfigError, load_config
from susanoo.discovery import build_inventory
from susanoo.enforcement.auto import auto_settings
from susanoo.enforcement.hooks import HOOK_EVENTS, handle
from susanoo.enforcement.hooks import _project_root as _hook_root
from susanoo.enforcement.state import evidence_status
from susanoo.entry import _project_has_own_hooks
from susanoo.evaluation.report import OUTCOME_STATUSES, latest_by_record, summarize
from susanoo.evaluation.store import RecordError, RecordStore, new_record_id
from susanoo.evaluation.suite import run_suite
from susanoo.paths import (
    OFF_FILE,
    find_project_root,
    global_installed,
    home_state,
    is_initialized,
    project_mode,
    records_dir,
    state_dir,
)
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


def _init_global(ctx: Context) -> int:
    """`susanoo init --global`: install (or remove) Susanoo as the default workflow everywhere."""
    args = ctx.args
    if args.uninstall:
        actions = plan_global_uninstall()
        _refuse_settings_conflict(next((a for a in actions if a.path == "settings.json"), None), want=False)
        if not args.dry_run:
            actions = apply_global_uninstall(actions)
    else:
        auto = not args.no_auto
        actions = plan_global(auto=auto, with_agents=not args.no_agents, force=args.force)
        _refuse_settings_conflict(next((a for a in actions if a.path == "settings.json"), None), want=True)
        if not args.dry_run:
            actions = apply_global(actions, auto=auto, with_agents=not args.no_agents)
    if args.json:
        ctx.emit_json({"global": True, "dry_run": args.dry_run, "actions": actions_to_dicts(actions)})
        return EXIT_OK
    title = "global uninstall" if args.uninstall else "global install"
    lines = [f"Susanoo {title} in {claude_home()}" + (" (dry run)" if args.dry_run else ""), ""]
    lines += [f"  {a.action:<14} {a.path}" + (f"  ({a.note})" if a.note else "") for a in actions]
    lines.append("")
    if args.dry_run:
        lines.append("No files were written (dry run). Re-run without --dry-run to apply.")
    elif args.uninstall:
        lines.append(f"Records in {home_state()} were kept; delete them yourself if you no longer need them.")
    else:
        lines += [
            "Coding tasks in every project now go through Susanoo"
            + (" automatically." if not args.no_auto else " when you use /susanoo."),
            f"Records: {home_state() / 'projects'}. Turn it off in one project with a {OFF_FILE} file.",
            "Restart running Claude Code sessions so they load the new hooks.",
        ]
        if user_command() != "susanoo":
            lines.append(
                "Note: `susanoo` is not on PATH, so the playbook uses the full command. Installing it "
                "on PATH (for example `python -m pip install --user -e <path-to-susanoo>`) makes it shorter."
            )
    ctx.emit("\n".join(lines))
    return EXIT_OK


def cmd_init(ctx: Context, stdin: TextIO) -> int:
    args = ctx.args
    if args.global_install:
        return _init_global(ctx)
    notes: list[str] = []
    manifest = read_manifest(ctx.root)
    managed = manifest.get("settings_managed") is True
    created = manifest.get("settings_created") is True
    migration: list[FileAction] = []
    if args.uninstall:
        own = plan_uninstall(ctx.root)
        legacy = plan_legacy_files(ctx.root)
        settings = plan_settings(ctx.root, want=False, managed=managed, created=created)
        _refuse_settings_conflict(settings, want=False)
        if not args.dry_run:
            # Settings first, while the manifest that records Susanoo's ownership still exists.
            settings = _apply_settings(ctx.root, want=False, managed=managed, created=created)[0]
            apply_uninstall(ctx.root, own)
            remove_legacy_files(ctx.root, legacy)
        actions = own + legacy + ([settings] if settings else [])
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
        scope = args.enforce_scope or enforcement_scope(ctx.root)
        if args.enforce_scope and not enforce:
            ctx.err.write("note: --enforce-scope was saved; it takes effect with --enforce\n")
        want_settings = enforce and scope == "settings"
        skill_hooks = enforce and scope == "skill"
        # With the skill scope, a compaction hook Susanoo put in settings earlier stays: the skill
        # cannot provide it, and it cannot run twice.
        keep = frozenset({"SessionStart"}) if skill_hooks else frozenset()
        # Automatic prompt routing needs settings-scoped hooks (skill hooks start only with /susanoo).
        auto = want_settings and (args.auto or (manifest.get("auto") is True and not args.no_auto))
        if args.auto and not want_settings:
            ctx.err.write("note: --auto needs --enforce --enforce-scope settings; it was not turned on\n")
        hooks = settings_hooks(auto=auto)
        settings = plan_settings(
            ctx.root, want=want_settings, managed=managed, created=created, keep=keep, hooks=hooks
        )
        skill_plan = plan_install(ctx.root, force=args.force, with_agents=False, enforce=skill_hooks)[0]
        _check_single_hook_place(ctx, enforce, want_settings, skill_hooks, settings, skill_plan)
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
                enforce=skill_hooks,
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
            # Settings first: if they cannot be written, the skill and manifest stay as they were.
            settings, created = _apply_settings(
                ctx.root, want=want_settings, managed=managed, created=created, keep=keep, hooks=hooks
            )
            update_manifest(
                ctx.root,
                auto=auto,
                settings_created=created,
                settings_managed=bool(owned_events_in(ctx.root / SETTINGS_FILE))
                and (want_settings or managed),
            )
            install = plan_install(
                ctx.root, force=args.force, with_agents=not args.no_agents, enforce=skill_hooks
            )
            apply_install(ctx.root, install, with_agents=not args.no_agents, enforce=enforce, scope=scope)
            remove_legacy_files(ctx.root, migration)
        actions = migration + install + ([settings] if settings else [])
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
    conflicts = [a for a in actions if a.action == "conflict" and a.path != SETTINGS_FILE]
    if conflicts:
        lines.append(f"{len(conflicts)} file(s) left untouched because they differ from Susanoo's version.")
    if any(a.action in ("migrate", "merge", "remove") for a in migration):
        verb = "Will migrate" if args.dry_run else "Migrated"
        lines.append(f"{verb} the DevRoute installation; DevRoute is now Susanoo.")
    lines += notes
    if args.uninstall:
        if state_dir(ctx.root).is_dir():
            lines.append(
                "Local records in .susanoo/records were kept; delete them yourself if you no longer need them."
            )
    elif not args.dry_run:
        touched = any(a.path == SETTINGS_FILE and a.action not in ("unchanged", "conflict") for a in actions)
        lines += [
            f"Only Susanoo's hook entries in {SETTINGS_FILE} were changed; CLAUDE.md and MCP configuration "
            "were not modified."
            if touched
            else "Settings, CLAUDE.md, and MCP configuration were not modified.",
            "Next: run `susanoo doctor`, then in Claude Code: /susanoo <task description>",
        ]
    else:
        lines.append("No files were written (dry run). Re-run without --dry-run to apply.")
    ctx.emit("\n".join(lines))
    return EXIT_OK


def _settings_sources(root: Path) -> list[Path]:
    """Settings files Claude Code reads hooks from for this project (each file once)."""
    paths = [
        root / SETTINGS_FILE,
        root / ".claude" / "settings.json",
        claude_home() / "settings.json",
    ]
    unique: dict[Path, Path] = {}
    for path in paths:
        unique.setdefault(path.resolve(), path)  # the project may be the home directory
    return list(unique.values())


def _refuse_settings_conflict(settings: FileAction | None, *, want: bool) -> None:
    """A settings file Susanoo has to change but cannot is an error, never a silent skip."""
    if settings and settings.action == "conflict":
        verb = "add the enforcement hooks to" if want else "remove Susanoo's hooks from"
        problem = settings.note.split(";")[0]
        raise CLIError(
            f"cannot {verb} {settings.path}: it {problem}. Fix the file first"
            + ("." if want else ", or remove the `susanoo hook` entries yourself.")
            + " Nothing was changed."
        )


def _check_single_hook_place(
    ctx: Context,
    enforce: bool,
    want_settings: bool,
    skill_hooks: bool,
    settings: FileAction | None,
    skill_plan: FileAction,
) -> None:
    """Refuse, before any change, an install that would leave enforcement hooks in two places.

    Claude Code does not deduplicate identical hooks, so every gate would run twice.
    """
    _refuse_settings_conflict(settings, want=want_settings)
    if not enforce:
        return
    unchanged = "Nothing was changed."
    if want_settings and skill_plan.action == "conflict":
        current = _read(ctx.root / skill_plan.path)
        if current == UNREADABLE:
            raise CLIError(
                f"{skill_plan.path} is not a readable text file. Fix or remove it first. {unchanged}"
            )
        if "susanoo hook" in str(current):
            raise CLIError(
                "the /susanoo skill has local changes and still declares the enforcement hooks, so they "
                "would also run from settings. Remove its `hooks:` block, or rerun with --force (a backup "
                f"is written first). {unchanged}"
            )
    removing = settings is not None and settings.action in ("remove", "remove-hooks")
    if skill_hooks and not removing and owned_events_in(ctx.root / SETTINGS_FILE) & GATE_EVENTS:
        raise CLIError(
            f"{SETTINGS_FILE} already runs Susanoo's hooks, so the skill's would run twice. Use "
            f"--enforce-scope settings, or remove them from that file. {unchanged}"
        )
    elsewhere = [
        str(p)
        for p in _settings_sources(ctx.root)[1:]
        if owned_events_in(p, include_global=False) & GATE_EVENTS
    ]
    if elsewhere:
        raise CLIError(
            f"{', '.join(elsewhere)} already runs Susanoo's hooks, so enforcement would run them twice. "
            f"Remove them there, or use --no-enforce. {unchanged}"
        )


def _apply_settings(root: Path, **kwargs: Any) -> tuple[FileAction | None, bool]:
    try:
        return apply_settings(root, **kwargs)
    except OSError as exc:
        raise CLIError(f"could not update {SETTINGS_FILE}: {exc}") from exc


def _check_hook_placement(ctx: Context, check: Callable[[str, str, str], None], *, skill_hooks: bool) -> None:
    """Report where enforcement hooks live, and warn when the same events run twice."""
    local = ctx.root / SETTINGS_FILE
    settings_gates = [
        p for p in _settings_sources(ctx.root) if owned_events_in(p, include_global=False) & GATE_EVENTS
    ]
    places = (["the /susanoo skill"] if skill_hooks else []) + [str(p) for p in settings_gates]
    if len(places) > 1:
        check(
            "enforcement",
            "warn",
            f"hooks are in {' and '.join(places)}; Claude Code runs each, so every gate runs twice. "
            "Keep one: `susanoo init --enforce-scope skill|settings`, and remove any you added by hand",
        )
    elif settings_gates:
        check("enforcement", "ok", f"hooks on (in {settings_gates[0]})")
    elif enforcement_scope(ctx.root) == "settings" and enforcement_enabled(ctx.root):
        missing = ", ".join(sorted(GATE_EVENTS - owned_events_in(local)))
        check(
            "enforcement",
            "warn",
            f"settings scope is recorded, but {SETTINGS_FILE} lacks {missing}; run `susanoo init`",
        )
    if (skill_hooks or settings_gates) and not shutil.which("susanoo"):
        check(
            "enforcement",
            "warn",
            "hooks call `susanoo`, which is not on PATH; they will fail open until it is",
        )


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

    if global_installed():
        routing = auto_settings(ctx.root)
        if (ctx.root / OFF_FILE).exists():
            detail = f"installed; off in this project ({OFF_FILE})"
        elif project_mode(ctx.root) and _project_has_own_hooks(ctx.root):
            detail = "installed; this project runs its own Susanoo hooks, so the global hooks step aside here"
            routing = {}
        elif project_mode(ctx.root):
            detail = "installed; this project uses its own .susanoo/ setup"
            if enforcement_enabled(ctx.root) and enforcement_scope(ctx.root) == "skill":
                check(
                    "global install",
                    "warn",
                    "this project enforces through its /susanoo skill, so after `/susanoo` the gates can run "
                    "twice (global and skill). Run `susanoo init --enforce-scope settings` here (the global hooks "
                    "then step aside) or `susanoo init --no-enforce`",
                )
        else:
            detail = f"on for this project; records in {records_dir(ctx.root).parent}"
        detail += (
            "; coding prompts are routed automatically"
            if routing
            else "; prompts are not routed automatically"
        )
        check("global install", "ok", detail)
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
    else:
        enforced = "susanoo hook" in skill.read_text(encoding="utf-8")
        check(
            "/susanoo skill",
            "warn",
            "installed but differs from this Susanoo version (modified or outdated); `susanoo init` shows options",
        )
    _check_hook_placement(ctx, check, skill_hooks=skill.is_file() and enforced)
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
    if not project_mode(ctx.root) and is_initialized(ctx.root):
        check("local records", "ok", f"global mode; records in {records_dir(ctx.root).parent}")
    elif is_initialized(ctx.root):
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
    hook_root = _hook_root(event)
    if ctx.args.from_global and project_mode(hook_root) and _project_has_own_hooks(hook_root):
        return EXIT_OK  # the project's own hooks act; running both would double every gate
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
        help="turn on enforcement hooks (plan-approval, evidence, irreversible-action gates)",
    )
    p.add_argument(
        "--enforce-scope",
        choices=["skill", "settings"],
        help="where the hooks live: the /susanoo skill (default; only that Claude Code process) or "
        ".claude/settings.local.json (every session, including resumed ones); the choice is kept",
    )
    p.add_argument("--no-enforce", action="store_true", help="install the skill without enforcement hooks")
    p.add_argument(
        "--global",
        dest="global_install",
        action="store_true",
        help="install for every project in your Claude Code user configuration (~/.claude): playbook, "
        "skill, subagents, and hooks; coding tasks are routed automatically",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="with --enforce --enforce-scope settings: route coding prompts in this project automatically",
    )
    p.add_argument(
        "--no-auto",
        action="store_true",
        help="do not route prompts automatically; the gates stay (also for --global)",
    )
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
    p.add_argument("--global", dest="from_global", action="store_true", help="called by the global install")
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
