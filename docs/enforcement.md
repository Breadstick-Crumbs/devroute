# Workflow enforcement (opt-in)

A Claude Code skill is a set of instructions, and a model can drift from instructions, especially in long sessions or after context compaction. DevRoute's enforcement mode turns the parts of a workflow that matter most into **deterministic checks** that Claude Code runs through hooks.

```bash
devroute init --enforce        # later `devroute init` runs keep this choice; --no-enforce turns it off
```

Enforcement requires the `devroute` command on `PATH` in the environment Claude Code runs in. `devroute doctor` warns if it is missing.

## How it is wired

`--enforce` adds a `hooks:` block to the `/devroute` skill's frontmatter. Claude Code registers skill hooks **when the skill is invoked**, and they stay active for the rest of that session. As a result:

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
| **Compaction recovery** | `SessionStart` (source `compact`), `PostCompact` | After context compaction, a short summary of the active route (profile, approval state, controls, outstanding verification) is put back into Claude's context. See the limits below. |
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

## Limits

- **Guardrails, not a sandbox.** The gates catch drift; they are not a security boundary against a determined agent. Shell-write detection is pattern-based and cannot recognize every way a command can change files.
- **Permission modes.** The Claude Code docs confirm that a hook `ask` still prompts in auto mode. How `ask` behaves in `bypassPermissions` and `dontAsk` modes is not documented, so do not rely on the approval prompt in those modes. The plan-approval gate itself uses `deny`, which does not depend on a prompt being shown.
- **Real Claude Code sessions.** The gate logic and the hook bridge are tested end to end with recorded hook payloads. Behavior inside live Claude Code sessions is less tested and depends on Claude Code's hook implementation.
- **`/clear`** gives the session a new id, which ends enforcement for routes created before it.
- **Compaction hooks from a skill are unconfirmed.** The Claude Code docs document hooks in skill frontmatter (with a `PreToolUse` example) but do not list which events are supported there, so it is unconfirmed whether `SessionStart` and `PostCompact` declared in a skill are honored. If they are not, compaction recovery silently does nothing; the other gates are unaffected. To be safe, you can add the session-start hook to your own `.claude/settings.local.json` yourself:

  ```json
  {"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": "devroute hook session-start"}]}]}}
  ```

  It does nothing in sessions without an active route.
