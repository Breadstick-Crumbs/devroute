# Workflow enforcement (opt-in)

A Claude Code skill is a set of instructions, and a model can drift from instructions, especially in long sessions or after context compaction. Susanoo's enforcement mode turns the parts of a workflow that matter most into **deterministic checks** that Claude Code runs through hooks.

```bash
susanoo init --enforce                           # hooks in the /susanoo skill (default scope)
susanoo init --enforce --enforce-scope settings  # hooks in .claude/settings.local.json
```

Later `susanoo init` runs keep both choices; `--no-enforce` turns enforcement off. Enforcement requires the `susanoo` command on `PATH` in the environment Claude Code runs in. `susanoo doctor` warns if it is missing.

## How it is wired

Hooks live in exactly one place, chosen with `--enforce-scope`. Claude Code does not deduplicate identical hooks from a skill and a settings file, so every gate would run twice. `susanoo init` therefore refuses, before changing anything, when the result would put the gates in two places: a modified `/susanoo` skill that still has hooks while you choose `settings`, Susanoo hooks you added to a settings file by hand while you choose `skill`, or Susanoo hooks in `.claude/settings.json` or `~/.claude/settings.json`. `susanoo doctor` reports where the hooks are and warns when it finds them in more than one place.

| | `skill` (default) | `settings` |
|---|---|---|
| Where | `PreToolUse` and `Stop` hooks in the `/susanoo` skill's frontmatter | `PreToolUse`, `Stop`, and `SessionStart` (compact) hooks in `.claude/settings.local.json` |
| Active | From the moment `/susanoo` is invoked until that Claude Code process exits, including across compaction | In every Claude Code session in the project |
| Resumed sessions (`claude --resume`, `--continue`) | **Not enforced** | Enforced |
| Compaction recovery | Needs the [settings snippet](#compaction-recovery) | Included |
| Settings files | Never touched | Only Susanoo's own hook entries (commands starting with `susanoo hook`) in `settings.local.json` are added or removed; everything else in the file is kept. A file that is not valid JSON, has an unexpected shape, or is a symbolic link is never rewritten; `init` stops instead. |
| Cost in sessions without a route | None | Every Edit, Write, and shell command, and the end of every turn (`Stop`), starts `susanoo hook`, which finds no route and allows it (a short Python start-up each time). With `hands_off = false`, `susanoo approve` and `--accept-missing-evidence` always ask, in any session. |

`.claude/settings.local.json` is your personal settings file. Claude Code normally keeps it out of git; check that yours is ignored before committing.

Switching back to the skill scope removes Susanoo's `PreToolUse` and `Stop` entries from the settings file and keeps its `SessionStart` (compact) entry, because the skill cannot provide compaction recovery and that event cannot run twice. `--no-enforce` and `susanoo init --uninstall` remove all of Susanoo's entries, and delete the file if Susanoo created it and nothing else is in it.

Claude Code reads hooks from settings files when a session starts, and may ask you to review changed hooks in `/hooks` first. After changing the scope, restart running Claude Code sessions in the project so they pick up the change; until then a session can still run the hooks from the old place.

In both scopes, each route is bound to the Claude Code session that created it, through the `CLAUDE_CODE_SESSION_ID` variable that Claude Code sets for shell commands, so hooks in one session never act on another session's route. A resumed session keeps its session id, which is why settings-scoped hooks still find its route.

Every hook calls `susanoo hook <event>`. That command reads the event JSON, looks up the session's active route in `.susanoo/records/`, and answers in Claude Code's hook format. A route stays active until an outcome is recorded for it.

## The gates

| Gate | Hook | Behavior |
|---|---|---|
| **Plan approval** | `PreToolUse` on Edit/Write/MultiEdit/NotebookEdit | In STANDARD and DEEP, edits to files inside the project are **denied** until the plan is approved. Claude receives the reason, so it presents the plan instead. Reading and searching stay allowed, and so do edits outside the project. |
| **Approval integrity** | `PreToolUse` on Bash/PowerShell | With `hands_off = false`, `susanoo approve` always gets `ask`, which means a permission prompt you have to accept, so Claude cannot approve its own plan; the same applies to `susanoo outcome --accept-missing-evidence`. Hands-off (the default) needs no approval, and `--accept-missing-evidence` is refused instead of asked about. |
| **Shell writes before approval** | `PreToolUse` on Bash/PowerShell | Before approval, commands that look like they modify files or the environment (redirection, `sed -i`, `rm`, `git commit`, package installs, ...) get `ask` instead of running silently. |
| **Irreversible actions** | `PreToolUse` on Bash/PowerShell | While the `irreversible-action-gate` control is active, pushes, history rewrites, deploys, infrastructure apply/destroy, publishing, migrations, and destructive SQL get `ask`. |
| **Evidence** | `Stop` | Once work has started (a file was changed) and the plan, if one is required, is approved, Claude cannot end the turn while required verification lacks passing evidence. Runnable checks need a passing result that Susanoo executed itself. Checks done by hand need a reported `passed`, or a reported `skipped` with the reason. A reported `failed` does not count. Ending a turn to present a plan or ask a question is never blocked. |
| **Compaction recovery** | `SessionStart` (source `compact`) | After context compaction, a short summary of the active route (profile, approval state, controls, outstanding verification) is put back into Claude's context. Included in the settings scope. Claude Code does not fire this event for hooks declared in a skill, so the skill scope needs the [settings snippet](#compaction-recovery). The other gates keep working after compaction without it. |
| **Route integrity** | `PreToolUse` on Bash/PowerShell | While a plan is unapproved, closing the route (`susanoo outcome`) or replacing it (`susanoo route`) needs your confirmation, so the gates cannot be dropped silently. |

## Autonomy

Susanoo is **hands-off by default** (`[enforcement] hands_off = true`): in every permission mode it adds no approvals and no questions, and Claude Code's own permission settings decide what needs confirmation (deploys, pushes, file edits). Only the evidence gate stays, because it never asks anything. The briefs follow suit: checkpoints are titled "Checkpoints" and are reported, not waited at.

To get the approval, shell-write, irreversible-action, and route-integrity gates back, set `hands_off = false`. They then apply like this:

- **Normal (default) permission mode:** the gates above apply. That is at most one check-in per task, the plan approval for STANDARD and DEEP, plus confirmation for irreversible commands while that control is active.
- **Autonomous mode:** when Claude Code runs in one of `[enforcement] autonomous_modes` (default: acceptEdits, auto, dontAsk, bypassPermissions), every Susanoo approval and question is off, and Claude Code's own permission settings decide what needs confirmation. The mode is read from each hook event, so switching modes mid-task takes effect immediately. Recording a task as completed without evidence (`--accept-missing-evidence`) is refused in these modes rather than asked about.
- **Autonomy asked for in words:** when an automatically routed task says "work autonomously", "don't ask", or similar (at the start or end of the message, outside quotes; also in a follow-up), that counts as approving the plan. The irreversible-action confirmation stays, because only a permission mode hands everything to Claude Code.
- **In every mode** the evidence gate stays: Claude cannot end the turn while required checks lack passing evidence. It never asks you anything.

With `hands_off = false`, also set `autonomous_modes = []` to keep every gate in every mode.

Related integrity rule, active with or without enforcement: `susanoo outcome --status completed --source agent` is refused while required verification lacks passing evidence. People recording outcomes themselves (`--source human`) are trusted.

## Approving a plan

When Claude presents the plan and you agree, Claude runs `susanoo approve`, and Claude Code asks you to confirm. You can also run it yourself in a terminal.

## Safety valves

- **No infinite loops.** The evidence gate releases after `enforcement.max_stop_blocks` (default 3) consecutive blocks with nothing changing. Each release is logged to `.susanoo/records/enforcement.jsonl`, so `susanoo report` data shows the task ended without the required evidence.
- **Fail open.** If `susanoo` is missing or a hook errors, the hook exits with a non-blocking error, which Claude Code shows as a hook error notice. Work is never blocked by a broken hook.
- **Closing a route** releases every gate. Any recorded outcome closes it, including one recorded by the agent; before the plan is approved, closing it requires your confirmation. Agent-recorded outcomes keep a list of unmet verification, so `susanoo report` shows tasks that ended without evidence.

## Configuration

All gate settings live in `[enforcement]` (see `src/susanoo/data/defaults.toml`):

```toml
[enforcement]
hands_off = true          # false: the approval and confirmation gates apply (see Autonomy)
approval_profiles = ["standard", "deep"]
evidence_profiles = ["fast", "standard", "deep"]
max_stop_blocks = 3
autonomous_modes = ["acceptEdits", "auto", "dontAsk", "bypassPermissions"]
edit_tools = ["Edit", "Write", "MultiEdit", "NotebookEdit"]
shell_write_patterns = ['...']      # regexes, case-insensitive
irreversible_commands = ['...']
```

## Compaction recovery

The settings scope includes this hook. With the skill scope, Claude Code does not fire `SessionStart` or `PostCompact` hooks declared in a skill, so the route summary is not re-injected after compaction unless you add this hook to your own `.claude/settings.local.json` (Susanoo leaves a hand-added snippet alone in the skill scope):

```json
{"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": "susanoo hook session-start"}]}]}}
```

It does nothing in sessions without an active route. Without it, the gates still apply after compaction; only the reminder is missing.

## Live test results

`scripts/live_enforcement_check.py` runs each check in a throwaway project against a real headless Claude Code session, sending every turn to one process the way an interactive session works. It sets `hands_off = false` and `autonomous_modes = []` in those projects so the gates themselves are exercised in every permission mode; with the default settings, Susanoo skips them by design (see [Autonomy](#autonomy)). Results with **Claude Code 2.1.296** (Windows, Sonnet):

| Check | Result |
|---|---|
| Plan-approval gate (acceptEdits) | Pass: the edit was denied and the file was unchanged |
| `susanoo approve` with the command pre-allowed, in acceptEdits, auto, bypassPermissions, and dontAsk | Pass in all four: the permission mode did not override the hook's `ask`, so the command did not run headlessly and no approval was recorded |
| Evidence gate on `Stop` (acceptEdits) | Pass: the turn was blocked, then released after `max_stop_blocks` |
| Compaction recovery with the settings snippet | Pass: the route summary was re-injected after `/compact` |
| Plan-approval gate after `/compact` in the same process | Pass |
| `SessionStart`/`PostCompact` declared in the skill | Do not fire (observed before they were removed from the skill; see [Compaction recovery](#compaction-recovery)) |
| Plan-approval gate after `claude --resume` in a new process | **Fail**: no Susanoo hook runs, and the edit went through |

With `--enforce-scope settings` (`scripts/live_enforcement_check.py --scope settings`, same version):

| Check | Result |
|---|---|
| Plan-approval gate, approval `ask` in all four permission modes, `Stop` evidence gate | Pass, as with the skill scope; the `Stop` gate released after exactly `max_stop_blocks`, so it did not run twice |
| Compaction recovery without any manual snippet | Pass: the route summary was re-injected after `/compact`, and the edit gate still applied |
| Plan-approval gate after `claude --resume` in a new process | Pass: the edit was denied. Two earlier runs were inconclusive because Claude did not attempt the edit at all |

Automatic routing (`scripts/live_auto_check.py`, projects installed with `--enforce --enforce-scope settings --auto`, default autonomy settings, same version). None of the prompts mention Susanoo:

| Check | Result |
|---|---|
| A question ("What does app.py do?") | Pass: not routed |
| A task in default mode | Routed automatically. In two runs Claude presented the plan and waited for approval instead of editing, so the edit gate was not needed as a backstop (inconclusive as a gate test) |
| A task in acceptEdits mode | Pass: routed and built with no Susanoo approval or question. Claude Code's own permissions blocked the unapproved `susanoo verify` shell command headlessly, and the evidence gate then held the turn for `max_stop_blocks` |
| A task in bypassPermissions mode | Pass: routed, built without questions, `susanoo verify --execute` run (a failing test was fixed and rerun), outcome recorded as completed, no stop blocks needed |

These are single runs. Results can change with Claude Code versions; rerun the scripts after upgrading.

## Limits

- **Guardrails, not a sandbox.** The gates catch drift; they are not a security boundary against a determined agent. Shell-write detection is pattern-based and cannot recognize every way a command can change files.
- **Resumed sessions with the skill scope are not enforced.** Skill hooks live in the process that invoked `/susanoo`. If you quit and later continue the session with `claude --resume` or `--continue`, the route is still active in Susanoo's records but no hook runs, so none of the gates apply. Use `--enforce-scope settings` if you resume sessions, or start a new `/susanoo` task. Do not add the hooks to your settings by hand while the skill has them: Claude Code does not deduplicate identical hooks, so every gate would run twice and the `Stop` gate would release early.
- **Permission modes.** By default the hands-off modes skip Susanoo's approvals and questions on purpose (see [Autonomy](#autonomy)). With `autonomous_modes = []`, the live runs above show the approval `ask` holding in every permission mode, including bypassPermissions and dontAsk; the Claude Code docs do not document this for every mode, so treat it as observed behavior for the tested version.
- **Automatic routing is keyword-based.** It routes only prompts that start like a task ("add ...", "can you fix ..."); requests phrased differently ("the checkout page is broken") are not routed. Use `/susanoo <task>` for those, or reword.
- **Live coverage.** The gate logic and the hook bridge are tested end to end with recorded hook payloads. The live checks above cover the main gates in single runs; the shell-write, irreversible-action, and route-integrity gates use the same `ask` path but were not exercised live.
- **`/clear` and `--fork-session`** give the session a new id, which ends enforcement for routes created before it.
