# Workflow enforcement (opt-in)

A Claude Code skill is a set of instructions, and a model can drift from instructions, especially in long sessions or after context compaction. DevRoute's enforcement mode turns the parts of a workflow that matter most into **deterministic checks** that Claude Code runs through hooks.

```bash
devroute init --enforce        # later `devroute init` runs keep this choice; --no-enforce turns it off
```

Enforcement requires the `devroute` command on `PATH` in the environment Claude Code runs in. `devroute doctor` warns if it is missing.

## How it is wired

`--enforce` adds `PreToolUse` and `Stop` hooks to the `/devroute` skill's frontmatter. Claude Code registers skill hooks **when the skill is invoked**, and they stay active for as long as that Claude Code process keeps running, including across context compaction. A session resumed later in a new process (`claude --resume` or `--continue`) does not have them; see the limits below. As a result:

- no settings file is modified;
- sessions that never use `/devroute` are completely unaffected;
- each route is bound to the Claude Code session that created it, through the `CLAUDE_CODE_SESSION_ID` variable that Claude Code sets for shell commands, so hooks in one session never act on another session's route.

Every hook calls `devroute hook <event>`. That command reads the event JSON, looks up the session's active route in `.devroute/records/`, and answers in Claude Code's hook format. A route stays active until an outcome is recorded for it.

## The gates

| Gate | Hook | Behavior |
|---|---|---|
| **Plan approval** | `PreToolUse` on Edit/Write/MultiEdit/NotebookEdit | In STANDARD and DEEP, edits to files inside the project are **denied** until the plan is approved. Claude receives the reason, so it presents the plan instead. Reading and searching stay allowed, and so do edits outside the project. |
| **Approval integrity** | `PreToolUse` on Bash/PowerShell | `devroute approve` always gets `ask`, which means a permission prompt you have to accept. Claude cannot approve its own plan. The same applies to `devroute outcome --accept-missing-evidence`. |
| **Shell writes before approval** | `PreToolUse` on Bash/PowerShell | Before approval, commands that look like they modify files or the environment (redirection, `sed -i`, `rm`, `git commit`, package installs, ...) get `ask` instead of running silently. |
| **Irreversible actions** | `PreToolUse` on Bash/PowerShell | While the `irreversible-action-gate` control is active, pushes, history rewrites, deploys, infrastructure apply/destroy, publishing, migrations, and destructive SQL get `ask`. |
| **Evidence** | `Stop` | Once work has started (a file was changed) and the plan, if one is required, is approved, Claude cannot end the turn while required verification lacks passing evidence. Runnable checks need a passing result that DevRoute executed itself. Checks done by hand need a reported `passed`, or a reported `skipped` with the reason. A reported `failed` does not count. Ending a turn to present a plan or ask a question is never blocked. |
| **Compaction recovery** (settings snippet) | `SessionStart` (source `compact`) | After context compaction, a short summary of the active route (profile, approval state, controls, outstanding verification) is put back into Claude's context. Claude Code does not fire this event for hooks declared in a skill, so this one needs a hook in your settings; see [Compaction recovery](#compaction-recovery). The other gates keep working after compaction without it. |
| **Route integrity** | `PreToolUse` on Bash/PowerShell | While a plan is unapproved, closing the route (`devroute outcome`) or replacing it (`devroute route`) needs your confirmation, so the gates cannot be dropped silently. |

Related integrity rule, active with or without enforcement: `devroute outcome --status completed --source agent` is refused while required verification lacks passing evidence. People recording outcomes themselves (`--source human`) are trusted.

## Approving a plan

When Claude presents the plan and you agree, Claude runs `devroute approve`, and Claude Code asks you to confirm. You can also run it yourself in a terminal.

## Safety valves

- **No infinite loops.** The evidence gate releases after `enforcement.max_stop_blocks` (default 3) consecutive blocks with nothing changing. Each release is logged to `.devroute/records/enforcement.jsonl`, so `devroute report` data shows the task ended without the required evidence.
- **Fail open.** If `devroute` is missing or a hook errors, the hook exits with a non-blocking error, which Claude Code shows as a hook error notice. Work is never blocked by a broken hook.
- **Closing a route** releases every gate. Any recorded outcome closes it, including one recorded by the agent; before the plan is approved, closing it requires your confirmation. Agent-recorded outcomes keep a list of unmet verification, so `devroute report` shows tasks that ended without evidence.

## Configuration

All gate settings live in `[enforcement]` (see `src/devroute/data/defaults.toml`):

```toml
[enforcement]
approval_profiles = ["standard", "deep"]
evidence_profiles = ["fast", "standard", "deep"]
max_stop_blocks = 3
edit_tools = ["Edit", "Write", "MultiEdit", "NotebookEdit"]
shell_write_patterns = ['...']      # regexes, case-insensitive
irreversible_commands = ['...']
```

## Compaction recovery

Claude Code does not fire `SessionStart` or `PostCompact` hooks declared in a skill, so the route summary is not re-injected after compaction unless you add this hook to your own `.claude/settings.local.json`:

```json
{"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": "devroute hook session-start"}]}]}}
```

It does nothing in sessions without an active route. Without it, the gates still apply after compaction; only the reminder is missing.

## Live test results

`scripts/live_enforcement_check.py` runs each check in a throwaway project against a real headless Claude Code session, sending every turn to one process the way an interactive session works. Results with **Claude Code 2.1.296** (Windows, Sonnet):

| Check | Result |
|---|---|
| Plan-approval gate (acceptEdits) | Pass: the edit was denied and the file was unchanged |
| `devroute approve` with the command pre-allowed, in acceptEdits, auto, bypassPermissions, and dontAsk | Pass in all four: the permission mode did not override the hook's `ask`, so the command did not run headlessly and no approval was recorded |
| Evidence gate on `Stop` (acceptEdits) | Pass: the turn was blocked, then released after `max_stop_blocks` |
| Compaction recovery with the settings snippet | Pass: the route summary was re-injected after `/compact` |
| Plan-approval gate after `/compact` in the same process | Pass |
| `SessionStart`/`PostCompact` declared in the skill | Do not fire (observed before they were removed from the skill; see [Compaction recovery](#compaction-recovery)) |
| Plan-approval gate after `claude --resume` in a new process | **Fail**: no DevRoute hook runs, and the edit went through |

These are single runs. Results can change with Claude Code versions; rerun the script after upgrading.

## Limits

- **Guardrails, not a sandbox.** The gates catch drift; they are not a security boundary against a determined agent. Shell-write detection is pattern-based and cannot recognize every way a command can change files.
- **Resumed sessions are not enforced.** Skill hooks live in the process that invoked `/devroute`. If you quit and later continue the session with `claude --resume` or `--continue`, the route is still active in DevRoute's records but no hook runs, so none of the gates apply. Start a new `/devroute` task instead. Adding the `PreToolUse` and `Stop` hooks to your settings closes this gap but is not recommended yet: Claude Code does not deduplicate them against the skill's identical hooks, so every hook would run twice and the `Stop` gate would release early. Settings-scoped enforcement is planned as an option for a later release ([#29](https://github.com/Breadstick-Crumbs/devroute/issues/29)).
- **Permission modes.** In the live runs above the approval `ask` held in every permission mode, including bypassPermissions and dontAsk. The Claude Code docs do not document this for every mode, so treat it as observed behavior for the tested version. The plan-approval gate uses `deny`, which does not depend on a prompt being shown.
- **Live coverage.** The gate logic and the hook bridge are tested end to end with recorded hook payloads. The live checks above cover the main gates in single runs; the shell-write, irreversible-action, and route-integrity gates use the same `ask` path but were not exercised live.
- **`/clear`** gives the session a new id, which ends enforcement for routes created before it.
