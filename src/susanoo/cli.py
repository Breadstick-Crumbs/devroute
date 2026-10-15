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
from susanoo.adapters.claude_code.settings_hooks import (
    GATE_EVENTS,
    SETTINGS_FILE,
    apply_settings,
    owned_events_in,
    plan_settings,
    settings_hooks,
)
from susanoo.adapters.claude_code.usage import fill_outcome
from susanoo.analyzer import Floor, assess
from susanoo.config import MAX_TIMEOUT_SECONDS, PROFILE_ORDER, Config, ConfigError, load_config
from susanoo.discovery import build_inventory
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.auto import (
    auto_settings,
    autonomy_requested,
    close_superseded,
    compact_brief,
    grant_autonomy,
    unfinished_work,
)
from susanoo.enforcement.hooks import HOOK_EVENTS, SessionState, handle
from susanoo.enforcement.hooks import _project_root as _hook_root
from susanoo.enforcement.state import active_route, evidence_status, find_active_route
from susanoo.entry import _project_has_own_hooks
from susanoo.evaluation.report import OUTCOME_STATUSES, latest_by_record, summarize
from susanoo.evaluation.store import RecordError, RecordStore, find_route, new_record_id
from susanoo.evaluation.suite import run_suite
from susanoo.land import LandError, LandOptions, land
from susanoo.land.gitops import common_dir
from susanoo.land.records import read_records
from susanoo.land.records import stats as land_stats
from susanoo.land.reviews import (
    REVIEW_STATUSES,
    REVIEW_TOOLS,
    check_tip,
    matching,
    read_reviews,
    record_review,
    repository,
    resolve_target,
)
from susanoo.paths import (
    OFF_FILE,
    find_project_root,
    global_installed,
    home_state,
    is_initialized,
    project_mode,
    records_dir,
    sibling_checkouts,
    state_dir,
)
from susanoo.pipeline import TaskPlan, message_floor, plan_task, project_context, route_floor, route_record
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
from susanoo.selection import SelectionContext
from susanoo.verification import EVIDENCE_STATUSES, execute_plan, reported_evidence
from susanoo.verification.commit import across, tree_state
from susanoo.verification.executor import redact

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


def _brief_command(root: Path) -> str:
    return str(auto_settings(root).get("command") or "susanoo")


def _session_route(ctx: Context, task: str, session: str, message: dict[str, Any]) -> int:
    """`susanoo route` in a session with automatic routing.

    Claude routes in its own words (or `/susanoo` routes the user's): the route is never lower
    than the user's latest message (its keyword floor) or than the open route it replaces, whose
    levels join the floor; record that route's outcome first to start fresh. A route with
    unfinished work is never replaced. ``--profile`` changes only the profile, and a profile
    below Susanoo's recommendation is refused unless the user's message named it.
    """
    skill = message.get("skill") is True
    store = RecordStore(ctx.root)
    record = not ctx.args.no_record and is_initialized(ctx.root)
    state = active_route(store, session) if is_initialized(ctx.root) else None
    if record and state is not None and unfinished_work(ctx.root, state, session):
        raise CLIError(
            f"route {state.record_id} has unfinished work: finish it, or record its outcome first "
            '(`susanoo outcome latest --status partial --source agent --notes "..."`)'
        )
    floor = Floor.merge(
        [prompt_state.floor(message), route_floor(state.route) if state is not None else None], PROFILE_ORDER
    )
    plan = plan_task(
        task,
        ctx.root,
        ctx.config,
        override=ctx.args.profile,
        verify_tools=ctx.args.verify_tools,
        floor=floor,
    )
    if state is not None and not set(state.controls) <= {c.id for c in plan.decision.controls}:
        raise CLIError(
            f"refusing to route: route {state.record_id} has safety controls this route would lose "
            f"({', '.join(sorted(set(state.controls) - {c.id for c in plan.decision.controls}))}). Record its "
            "outcome first if that task is finished."
        )
    recommended = plan.decision.recommended
    raw_named = message.get("named")
    named: list[Any] = raw_named if isinstance(raw_named, list) else []
    lighter = ctx.args.profile and PROFILE_ORDER.index(ctx.args.profile) < PROFILE_ORDER.index(recommended)
    if lighter and ctx.args.profile not in named:
        raise CLIError(
            f"only the user can choose a lighter workflow than {recommended.upper()}, and their message did "
            f"not name {ctx.args.profile.upper()}. Route without --profile, or ask the user; if they want "
            f'it, their message can say "use the {ctx.args.profile} workflow".'
        )
    hands_off = message.get("hands_off") is True
    if record:
        plan.record_id = new_record_id()
        data = route_record(plan, plan.record_id, ctx.config, session)
        extra: dict[str, Any] = {"floor": message.get("floor")}
        if not skill:
            if data.get("task"):
                data["task"] = redact(str(data["task"]))[:1000]
            # A route Claude makes keeps the replaced route's evidence rules (`auto` relaxes them).
            auto = state is None or state.route.get("auto") is True
            extra.update({"routed_by": "agent", "autonomous": hands_off, **({"auto": True} if auto else {})})
        if state is not None:
            extra["replaced"] = state.record_id
        data.update(extra)
        # The new route first, so the session never lacks an active route.
        store.append("routes", data)
        if state is not None:
            close_superseded(
                store, state, f"superseded by {plan.record_id} (a new route in this session)", ctx.config
            )
        if hands_off and not skill:
            grant_autonomy(store, plan.record_id, session)
    replaced = (
        f"This replaces route {state.record_id} ({state.profile.upper()}), and its levels are kept as a minimum. "
        "If that task was finished, record its outcome before routing a new one."
        if state is not None
        else ""
    )
    if ctx.args.json:
        ctx.emit_json(plan.to_dict(include_inventory=ctx.args.include_inventory))
    elif skill:
        ctx.emit(render_brief(plan) + (f"\n{replaced}" if replaced else ""))
    else:
        enforcement = ctx.config.get("enforcement", {})
        modes = set(enforcement.get("autonomous_modes", []))
        live_hands_off = (
            hands_off or bool(enforcement.get("hands_off")) or message.get("permission_mode") in modes
        )
        brief = compact_brief(
            plan,
            plan.record_id or "not recorded",
            live_hands_off,
            _brief_command(ctx.root),
            lead="from your description, never lower than the user's message",
            # Autonomy asked for earlier in the task still holds when Claude routes it anew.
            asked_autonomy=hands_off
            or (state is not None and autonomy_requested(store, state.route, state.record_id, {})),
        )
        ctx.emit(brief + (f"\n{replaced}" if replaced else ""))
    return EXIT_OK


def _refuse_extra_config(ctx: Context) -> None:
    """In a session with automatic routing, `--config` could switch off controls or signals."""
    if getattr(ctx.args, "config", None):
        raise CLIError(
            "--config is not accepted in a Claude Code session with automatic routing: it could weaken "
            "the route's safety controls. Ask the user to change Susanoo's configuration if needed."
        )


def cmd_route(ctx: Context, stdin: TextIO) -> int:
    task = _task_text(ctx.args, stdin)
    session = ctx.args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session and auto_settings(ctx.root):
        # Every route in such a session goes through the floors, even when the prompt record is
        # missing or unreadable (then only the open route is a floor and no profile is named).
        _refuse_extra_config(ctx)
        return _session_route(ctx, task, session, prompt_state.load(ctx.root, session) or {})
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
        actions = plan_global(
            auto=auto, with_agents=not args.no_agents, force=args.force, with_taste=not args.no_taste
        )
        _refuse_settings_conflict(next((a for a in actions if a.path == "settings.json"), None), want=True)
        if not args.dry_run:
            actions = apply_global(
                actions, auto=auto, with_agents=not args.no_agents, with_taste=not args.no_taste
            )
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
    manifest = read_manifest(ctx.root)
    managed = manifest.get("settings_managed") is True
    created = manifest.get("settings_created") is True
    if args.uninstall:
        own = plan_uninstall(ctx.root)
        settings = plan_settings(ctx.root, want=False, managed=managed, created=created)
        _refuse_settings_conflict(settings, want=False)
        if not args.dry_run:
            # Settings first, while the manifest that records Susanoo's ownership still exists.
            settings = _apply_settings(ctx.root, want=False, managed=managed, created=created)[0]
            apply_uninstall(ctx.root, own)
        actions = own + ([settings] if settings else [])
    else:
        enforce = True if args.enforce else False if args.no_enforce else enforcement_enabled(ctx.root)
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
            install = plan_install(
                ctx.root,
                force=args.force,
                with_agents=not args.no_agents,
                enforce=skill_hooks,
                with_taste=not args.no_taste,
            )
        else:
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
                ctx.root,
                force=args.force,
                with_agents=not args.no_agents,
                enforce=skill_hooks,
                with_taste=not args.no_taste,
            )
            apply_install(
                ctx.root,
                install,
                with_agents=not args.no_agents,
                enforce=enforce,
                scope=scope,
                with_taste=not args.no_taste,
            )
        actions = install + ([settings] if settings else [])
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
    store = RecordStore(ctx.root)
    if args.task or args.stdin:
        task = _task_text(args, stdin)
        override = args.profile
    else:
        try:
            store, record = find_route(ctx.root, args.record, _session())
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
                if s.withheld:
                    lines.append(f"  {'':<13} {s.withheld}")
            lines += ["", "Nothing was run. Add --execute to run the 'ready' steps and record evidence."]
            ctx.emit("\n".join(lines))
        return EXIT_OK

    only = set(args.step) if args.step else None
    timeout = args.timeout if args.timeout is not None else float(ctx.config["verify"]["timeout_seconds"])
    record_id = record.get("id") if record else None
    recorded = bool(record_id) and not args.no_record and is_initialized(store.root)
    # The commit the checks ran on, read before and after them (ADR 0015).
    before = tree_state(ctx.root) if recorded else None
    results = execute_plan(
        plan.verification, ctx.root, timeout=timeout, only=only, required_only=args.required_only
    )
    # The route may live in another checkout of this repository (see find_route): the checks ran
    # here, on this checkout's code, and the evidence says so.
    origin = _origin(ctx, store)
    evidence = [e.to_dict() for e in results]
    if recorded and before is not None:
        bound = across(before, tree_state(ctx.root)).to_dict()
        for data in evidence:
            data["record_id"] = str(record_id)
            data.update(origin)
            data.update(bound)
            store.append("evidence", data)
    if args.json:
        ctx.emit_json({"record_id": record_id, "evidence": evidence})
    else:
        ctx.emit(render_evidence(results))
        if recorded and origin:
            ctx.emit(
                f"Evidence recorded for route {record_id} in {store.root} (the checks ran in {ctx.root})."
            )
    required = {s.id for s in plan.verification.steps if s.level == "required"}
    failed = [e for e in results if e.step in required and e.status in ("failed", "error", "timeout")]
    return EXIT_FAIL if failed else EXIT_OK


def _timeout_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = float("nan")
    if not 0 < seconds <= MAX_TIMEOUT_SECONDS:  # also rejects nan and inf
        raise argparse.ArgumentTypeError(f"expected seconds greater than 0 and at most {MAX_TIMEOUT_SECONDS}")
    return seconds


def _session() -> str | None:
    """The Claude Code session running this command, if any (scopes `latest`)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _origin(ctx: Context, store: RecordStore) -> dict[str, str]:
    """Where evidence for a route in another checkout came from (its commit is ``head``)."""
    return {} if store.root == ctx.root else {"checkout": str(ctx.root)}


def _require_initialized(ctx: Context) -> RecordStore:
    if not is_initialized(ctx.root):
        raise CLIError("local records are disabled; run `susanoo init` first")
    return RecordStore(ctx.root)


def _route_and_store(ctx: Context) -> tuple[RecordStore, dict[str, Any]]:
    """The route named by ``ctx.args.record`` and the store holding it, here or in another checkout."""
    try:
        store, record = find_route(ctx.root, ctx.args.record, _session())
    except RecordError as exc:
        # A worktree without its own records is common; `susanoo init` is the remedy only when no
        # checkout of the repository has records.
        if not is_initialized(ctx.root) and not any(is_initialized(c) for c in sibling_checkouts(ctx.root)):
            raise CLIError("local records are disabled; run `susanoo init` first") from exc
        raise CLIError(str(exc)) from exc
    if not is_initialized(store.root):
        raise CLIError("local records are disabled; run `susanoo init` first")
    return store, record


def cmd_evidence(ctx: Context, stdin: TextIO) -> int:
    store, record = _route_and_store(ctx)
    try:
        item = reported_evidence(
            ctx.args.step, ctx.args.status, ctx.args.evidence_command, ctx.args.note or ""
        )
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    item.record_id = str(record["id"])
    # Susanoo reads the commit itself, when the report is made; the agent cannot supply it.
    data = {**item.to_dict(), **_origin(ctx, store), **tree_state(ctx.root).to_dict()}
    store.append("evidence", data)
    if ctx.args.json:
        ctx.emit_json(data)
    else:
        ctx.emit(
            f"Recorded reported evidence for {item.record_id}: {item.step} = {item.status} (source: reported)"
        )
    return EXIT_OK


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def cmd_outcome(ctx: Context, stdin: TextIO) -> int:
    store, record = _route_and_store(ctx)
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
    fill_outcome(data, record, store, ctx.config)
    store.append("outcomes", data)
    if a.json:
        ctx.emit_json(data)
    else:
        captured = data.get("captured", {}).get("fields", [])
        ctx.emit(
            f"Recorded outcome for {record['id']}: {a.status} (source: {a.source})"
            + (f"; captured from the session transcript: {', '.join(captured)}" if captured else "")
        )
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


def cmd_lead(ctx: Context, stdin: TextIO) -> int:
    """`susanoo lead on|off`: mark this Claude Code session as a lead, whose prompts are not routed.

    The mark lives in the hook state of this project, keyed by session id (ADR 0015). It changes
    only how the prompt hook treats this session's prompts; the gates of an open route still apply.
    Only the user turns it on: the marked session's latest message must be their `/susanoo-lead`,
    and inside a Claude Code session only that session can be marked. Without automatic routing
    there is nothing to stop, so no mark is written (none is left behind if routing is turned on
    later). Turning it off adds process, so it is never refused.
    """
    # The project the prompt hook will see (`CLAUDE_PROJECT_DIR`), not wherever the shell is now.
    root = ctx.root if ctx.args.path else _hook_root({})
    if not is_initialized(root):
        raise CLIError("local records are disabled; run `susanoo init` first")
    store = RecordStore(root)
    current = _session()
    session = ctx.args.session or current
    if not session:
        raise CLIError("no Claude Code session id: run this inside Claude Code, or pass --session <id>")
    on = ctx.args.lead_action == "on"
    routing = bool(auto_settings(root))
    before = prompt_state.is_lead(root, session)
    if on and routing and not before:
        if current and session != current:
            raise CLIError("inside a Claude Code session, only that session can be marked as a lead")
        if (prompt_state.load(root, session) or {}).get("lead_requested") is not True:
            raise CLIError(
                "a lead's prompts are not routed, so only the user turns it on: ask them to run "
                "/susanoo-lead in that session"
            )
    if on and not routing:
        lead = before  # nothing to stop, so nothing is marked
    else:
        try:
            prompt_state.set_lead(root, session, on, datetime.now(UTC).isoformat(timespec="seconds"))
        except OSError as exc:
            raise CLIError(f"could not {'mark' if on else 'unmark'} the session: {exc}") from exc
        lead = on
    state = active_route(store, session)
    if ctx.args.json:
        ctx.emit_json(
            {
                "session": session,
                "lead": lead,
                "was_lead": before,
                "automatic_routing": routing,
                "open_route": state.record_id if state is not None else None,
            }
        )
        return EXIT_OK
    if on:
        if routing:
            ctx.emit(
                ("This session was already a lead." if before else "This session is now a lead.")
                + " Its prompts are not routed; hand every change to a worker. `susanoo lead off` undoes it."
            )
        else:
            ctx.emit(
                "Automatic routing is off in this project, so this session's prompts are not routed anyway; "
                "nothing was marked. Act as the lead all the same. Susanoo's hooks may not be active, so "
                "nothing may ask the user before `susanoo land --merge`: ask them in chat before each merge."
            )
        if state is not None:
            ctx.emit(
                f"Route {state.record_id} is still open in this session and its gates still apply: "
                "finish it or record its outcome."
            )
    else:
        ctx.emit(
            "This session is no longer a lead; its prompts are routed again."
            if before
            else "This session was not a lead; nothing changed."
        )
    return EXIT_OK


def cmd_raise(ctx: Context, stdin: TextIO) -> int:
    """Raise the open route of this session to a stricter profile. Never lowers anything.

    The open route itself is the floor, and risk named in ``--reason`` ("deletes production
    data") is added to it, so its safety controls apply. The raised route replaces the open one
    and keeps its recorded evidence. The user's plan approval is kept too, unless the reason
    adds a safety control: then the user approves the plan again.

    The open route may be in another checkout of the repository (a git worktree, ADR 0017): the
    raised route then replaces it there, where that checkout's Stop hook reads it.
    """
    session = ctx.args.session or _session()
    if session and auto_settings(ctx.root):
        _refuse_extra_config(ctx)
    found = find_active_route(ctx.root, session) if session else None
    if not session or found is None:
        if not is_initialized(ctx.root) and not any(is_initialized(c) for c in sibling_checkouts(ctx.root)):
            raise CLIError("local records are disabled; run `susanoo init` first")
        raise CLIError("no open route in this Claude Code session to raise (route the task first)")
    store, state = found
    # The route's own checkout gates it: its configuration decides the safety side of the raise
    # (signals in the reason, approvals, records). The checks are planned here, like verify's.
    route_config = ctx.config
    if store.root != ctx.root:
        if auto_settings(store.root):
            _refuse_extra_config(ctx)
        extra = Path(ctx.args.config) if getattr(ctx.args, "config", None) else None
        route_config = load_config(store.root, extra)
    reason = redact(" ".join(ctx.args.reason.split()))[:500]
    if not reason:
        raise CLIError("give the reason for raising the route with --reason")
    old = state.record_id
    stored = state.route.get("task")
    task = stored if isinstance(stored, str) and stored.strip() else reason
    # The target is a minimum, not an override: a reason that routes higher wins.
    floor = Floor.merge(
        [
            route_floor(state.route),
            message_floor(reason, ctx.config),
            message_floor(reason, route_config) if route_config is not ctx.config else None,
            Floor({}, [], [], ctx.args.profile),
        ],
        PROFILE_ORDER,
    )
    plan = plan_task(task, ctx.root, ctx.config, floor=floor)
    controls = {c.id for c in plan.decision.controls}
    if not set(state.controls) <= controls:  # cannot happen: the floor keeps every level
        raise CLIError(f"refusing to raise route {old}: safety controls would be lost")
    added = sorted(controls - set(state.controls))
    target = plan.decision.profile
    if PROFILE_ORDER.index(target) <= PROFILE_ORDER.index(state.profile) and not added:
        ctx.emit(
            f"Route {old} already uses the {state.profile.upper()} workflow and the reason adds no safety "
            "control; routes are never lowered."
        )
        return EXIT_OK
    plan.record_id = new_record_id()
    data = route_record(plan, plan.record_id, route_config, session)
    data["task"] = stored if isinstance(stored, str) else None
    for key in ("auto", "routed_by", "floor", "autonomous"):
        if key in state.route:
            data[key] = state.route[key]
    data.update({"raised_from": old, "raise_reason": reason})
    store.append("routes", data)  # the new route first, so the session never lacks an active route
    close_superseded(store, state, f"raised to {target.upper()} as {plan.record_id}: {reason}", route_config)
    # The same task with more process: checks already run still count. A plan approval covers the
    # plan the user saw, and a raise always changes it (a stricter profile adds phases, a new control
    # adds a check), so only autonomy asked for in the user's own words carries over.
    for item in store.read("evidence"):
        if item.get("record_id") == old:
            store.append("evidence", {**item, "record_id": plan.record_id, "carried_from": old})
    for item in store.read("approvals"):
        if item.get("record_id") == old and item.get("checkpoint") == "autonomy":
            store.append("approvals", {**item, "record_id": plan.record_id, "carried_from": old})
    hook_state = SessionState(store.root, session)
    if hook_state.data.get("record_id") == old:
        hook_state.data["record_id"] = plan.record_id
        hook_state.save()
    if ctx.args.json:
        ctx.emit_json({"raised_from": old, "added_controls": added, **plan.to_dict()})
        return EXIT_OK
    header = f"Raised route {old} from {state.profile.upper()} to {target.upper()}: {reason}"
    approved = any(a.get("record_id") == plan.record_id for a in store.read("approvals"))
    if added:
        header += f"\nNew safety controls: {', '.join(added)}."
    enforcement = route_config.get("enforcement", {})
    configured_hands_off = bool(enforcement.get("hands_off"))
    if not approved and not configured_hands_off and target in set(enforcement.get("approval_profiles", [])):
        header += " The plan needs the user's approval again."
    if store.root != ctx.root:
        header += f"\nThe route was recorded in {store.root}; the raised route replaces it there."
    if state.route.get("auto") is True:
        modes = set(enforcement.get("autonomous_modes", []))
        message = prompt_state.load(store.root, session) or prompt_state.load(ctx.root, session) or {}
        hands_off = approved or configured_hands_off or message.get("permission_mode") in modes
        asked_autonomy = autonomy_requested(store, state.route, old, message)
        brief = compact_brief(
            plan,
            plan.record_id,
            hands_off,
            _brief_command(store.root),
            "again",
            asked_autonomy=asked_autonomy,
        )
        ctx.emit(header + "\n" + brief)
    else:
        ctx.emit(header + "\n\n" + render_brief(plan))
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
        context = SelectionContext.from_config(ctx.config, task=ctx.args.task or "")
        report = research(ctx.args.capability, inventory, registry, task=ctx.args.task, context=context)
    except ValueError as exc:
        raise CLIError(str(exc)) from exc
    if not ctx.args.no_record and is_initialized(ctx.root):
        RecordStore(ctx.root).append("research", report.to_dict())
    if ctx.args.json:
        ctx.emit_json(report.to_dict())
    else:
        ctx.emit(render_research(report))
    return EXIT_OK


def cmd_taste(ctx: Context, stdin: TextIO) -> int:
    """`susanoo taste path|shoot|capture`: the taste profile and screenshots for the taste-ui skill."""
    from susanoo import taste

    args = ctx.args
    try:
        location = taste.taste_location()
        if args.action == "path":
            if args.json:
                ctx.emit_json(location.to_dict())
            else:
                state = "profile found" if location.has_profile else "no TASTE.md yet"
                ctx.emit(
                    f"{location.path}\n  source: {location.source}\n  {state}; {len(location.sites)} captured site(s)"
                )
            return EXIT_OK
        if args.action == "shoot":
            if len(args.targets) != 2:
                raise CLIError("usage: susanoo taste shoot <page-url-or-html-file> <out-dir>")
            return taste.run_script("shoot.mjs", list(args.targets))
        if not args.targets:
            raise CLIError("usage: susanoo taste capture <slug>=<url> [...] [--out DIR]")
        out = Path(args.out) if args.out else location.path / "sites"
        return taste.run_script("capture.mjs", [str(out), *taste.parse_sites(list(args.targets))])
    except taste.TasteError as exc:
        raise CLIError(str(exc)) from exc


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


def cmd_land(ctx: Context, stdin: TextIO) -> int:
    args = ctx.args
    if args.stats:
        try:
            data = land_stats(read_records(common_dir(ctx.root)))
        except LandError as exc:
            raise CLIError(str(exc)) from exc
        if args.json:
            ctx.emit_json(data)
            return EXIT_OK
        culprits = ", ".join(f"{k} {v}" for k, v in sorted(data["culprits"].items())) or "none"
        overhead = data["median_overhead_s"]
        ctx.emit(
            "\n".join(
                [
                    f"land runs: {data['runs']} ({data['multi_candidate_runs']} with two or more candidates)",
                    "results: "
                    + (", ".join(f"{k} {v}" for k, v in sorted(data["results"].items())) or "none"),
                    f"conflicts kept both: {data['auto_resolved_runs']} runs; "
                    f"needed a manual merge: {data['unresolved_conflict_runs']} runs",
                    f"combined-tree failures by kind: {culprits}; flaky runs: {data['flaky_runs']}",
                    "median time on top of the checks: "
                    + (f"{overhead}s" if overhead is not None else "n/a"),
                    f"go or no-go ({data['rule']}, counts, not validated thresholds): {data['verdict']}",
                ]
            )
        )
        return EXIT_OK
    if not args.candidates:
        raise CLIError("name the pull requests or branches to land, e.g. `susanoo land 52 53`")
    if args.config:
        # The checks come from the base branch's configuration, so nothing can swap them per run.
        raise CLIError("land reads its configuration from the base branch; --config is not accepted")
    options = LandOptions(
        candidates=list(args.candidates),
        base=args.base,
        remote=args.remote,
        merge=args.merge,
        push=not args.no_push,
        timeout=args.timeout,
        reuse=not args.no_cache,
        keep_scratch=args.keep_scratch,
    )

    def say(line: str) -> None:
        # Flushed per line: a run takes minutes, and its output is often piped to a file.
        if not args.json:
            ctx.emit(line)
            ctx.out.flush()

    record, code = land(ctx.root, options, say)
    if args.json:
        ctx.emit_json(record)
    return code


def cmd_review(ctx: Context, stdin: TextIO) -> int:
    """Record a `/code-review` or `/security-review` against the commit it saw, or list records."""
    args = ctx.args
    if args.review_action == "record":
        target = resolve_target(ctx.root, args.target, args.remote)
        check_tip(args.tip, target)
        data = record_review(
            ctx.root, target, tool=args.tool, status=args.status, note=args.note or "", session_id=_session()
        )
        if args.json:
            ctx.emit_json(data)
        else:
            ctx.emit(f"Recorded {args.tool} {args.status} for {target.label} at {target.tip[:12]}.")
        return EXIT_OK
    reviews = read_reviews(repository(ctx.root))
    if args.target:
        reviews = matching(reviews, args.target)
    reviews = reviews[-args.last :] if args.last > 0 else []
    if args.json:
        ctx.emit_json(reviews)
        return EXIT_OK
    if not reviews:
        ctx.emit("No review records" + (f" for {args.target}." if args.target else "."))
        return EXIT_OK
    for r in reviews:
        label = f"#{r['pr']}" if r.get("pr") is not None else str(r.get("branch"))
        # Records are data: one line each, whatever a hand-edited file holds.
        note = " ".join(str(r.get("note") or "").split())[:120]
        ctx.emit(
            f"{r.get('recorded_at')}  {label}  {str(r.get('tip'))[:12]}  {r.get('tool')}  {r.get('status')}"
            + (f"  {note}" if note else "")
        )
    return EXIT_OK


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

    # Options are spelled out (no `--prof fast`), so what ran is what the records and logs show.
    p = sub.add_parser(
        "route",
        parents=[common],
        allow_abbrev=False,
        help="recommend a workflow and print the execution brief",
    )
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

    p = sub.add_parser(
        "raise",
        parents=[common],
        allow_abbrev=False,
        help="raise the open route of this session to a stricter workflow (never lowers)",
    )
    p.add_argument("profile", choices=PROFILE_ORDER[1:], help="the stricter workflow")
    p.add_argument("--reason", required=True, help="why the route understates the task (recorded)")
    p.add_argument("--session", help="Claude Code session id (default: $CLAUDE_CODE_SESSION_ID)")
    p.set_defaults(func=cmd_raise)

    p = sub.add_parser("init", parents=[common], help="install the /susanoo skill and project files safely")
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    p.add_argument("--force", action="store_true", help="replace differing files (a backup is written first)")
    p.add_argument("--no-agents", action="store_true", help="do not install the susanoo-* subagents")
    p.add_argument(
        "--no-taste",
        action="store_true",
        help="do not install the taste-ui skill (visual direction for UI work)",
    )
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
    p.add_argument(
        "--timeout",
        type=_timeout_seconds,
        help="per-command timeout in seconds (default: [verify] timeout_seconds in the configuration, 900)",
    )
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

    p = sub.add_parser(
        "lead",
        parents=[common],
        allow_abbrev=False,
        help="mark this Claude Code session as a lead (its prompts are not routed) or unmark it",
    )
    p.add_argument("lead_action", choices=["on", "off"], metavar="on|off", help="mark or unmark the session")
    p.add_argument("--session", help="Claude Code session id (default: $CLAUDE_CODE_SESSION_ID)")
    p.set_defaults(func=cmd_lead)

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

    p = sub.add_parser(
        "taste", parents=[common], help="the taste profile and screenshots used by the taste-ui skill"
    )
    p.add_argument(
        "action",
        choices=["path", "shoot", "capture"],
        help="path: where the profile is; shoot <page> <out-dir>: 375, 768 and 1280 px screenshots; "
        "capture <slug>=<url> ...: record reference sites for a profile",
    )
    p.add_argument(
        "targets", nargs="*", help="shoot: <page-url-or-html-file> <out-dir>; capture: <slug>=<url> ..."
    )
    p.add_argument("--out", help="capture: where to write the sites (default: <taste folder>/sites)")
    p.set_defaults(func=cmd_taste)

    p = sub.add_parser(
        "land",
        parents=[common],
        allow_abbrev=False,
        help="test pull requests or branches on one combined tree, then push; merge only with --merge",
    )
    p.add_argument("candidates", nargs="*", help="pull request numbers (52 or #52) or branch names, in order")
    p.add_argument("--base", help="base branch (default: the pull requests' base, else main)")
    p.add_argument("--remote", default="origin", help="remote to fetch from and push to (default origin)")
    p.add_argument(
        "--merge",
        action="store_true",
        help="merge the pull requests with gh after the checks pass; only the user gives this flag",
    )
    p.add_argument("--no-push", action="store_true", help="test only; push nothing")
    p.add_argument("--no-cache", action="store_true", help="rerun the checks even if this exact tree passed")
    p.add_argument(
        "--timeout", type=float, default=1800, help="per-command timeout in seconds (default 1800)"
    )
    p.add_argument("--keep-scratch", action="store_true", help="keep the scratch worktree for inspection")
    p.add_argument("--stats", action="store_true", help="summarize recorded land runs (ADR 0014 go or no-go)")
    p.set_defaults(func=cmd_land)

    p = sub.add_parser(
        "review",
        allow_abbrev=False,
        help="record a /code-review or /security-review against the commit it saw, or list them",
    )
    actions = p.add_subparsers(dest="review_action", metavar="<action>", required=True)
    q = actions.add_parser(
        "record",
        parents=[common],
        allow_abbrev=False,
        help="record a review; refused unless --tip is the branch's current tip",
    )
    q.add_argument("target", help="pull request number (52 or #52) or branch name")
    q.add_argument("--tip", required=True, help="the commit the review saw (full id or 7+ hex digits)")
    q.add_argument("--status", required=True, choices=REVIEW_STATUSES)
    q.add_argument("--tool", required=True, choices=REVIEW_TOOLS, help="the review command that was run")
    q.add_argument("--note", help="short summary of the findings")
    q.add_argument("--remote", default="origin", help="remote holding the branch (default origin)")
    q.set_defaults(func=cmd_review)
    q = actions.add_parser("list", parents=[common], allow_abbrev=False, help="show recorded reviews")
    q.add_argument("target", nargs="?", help="only this pull request number or branch name")
    q.add_argument("--last", type=int, default=20, help="number of recent records to show (default 20)")
    q.set_defaults(func=cmd_review)

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
    except (CLIError, ConfigError, RecordError, LandError, ValueError) as exc:
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
