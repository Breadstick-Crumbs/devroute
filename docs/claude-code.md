# Using Susanoo with Claude Code

## As your default workflow (every project)

```bash
python -m pip install --user -e /path/to/susanoo   # once, so `susanoo` works in every folder
susanoo init --global                               # --dry-run to preview, --uninstall to remove
```

After that, you never have to mention Susanoo. In every project you open in Claude Code:

- **Coding tasks are routed automatically.** A `UserPromptSubmit` hook classifies each prompt. A clear coding task ("add pagination to /users", "can you fix the login bug") is routed to FAST, STANDARD, or DEEP, and a short brief is added to Claude's context. Questions ("what does this do?", "review the auth code") and anything ambiguous are not routed; they get a one-line note instead, and Claude decides. If handling the message means changing code ("the checkout page is broken"), Claude runs `susanoo route "<task>"` itself. That route combines Claude's description with the keyword assessment of your message and is never lower than your message alone. A prompt starting with `?` is never routed and gets no note. Follow-ups continue the current task when they start with a continuation word ("also", "now", "then", ...) or arrive within 20 minutes of the task's last activity. Otherwise, or when the new task needs a stricter workflow, it gets its own route and the previous one is closed, unless the previous task has unfinished work: then it stays open and Claude is told to finish or record it first.
- **Claude routes before it edits.** If Claude starts editing a project file for a message that was not routed, with no route open, Susanoo stops that first edit once and tells Claude to run `susanoo route` first; a retry goes through. It never asks you anything. Turn it off with `[auto] route_before_edit = false`.
- **Claude can raise a route, never lower it.** If a brief understates the task, Claude runs `susanoo raise standard|deep --reason "<the risk>"`. Risk named in the reason turns on the matching safety controls. The stricter route keeps the evidence already recorded. Because a raise changes the plan, you approve the plan again when `hands_off = false`, unless you asked Claude to work autonomously. A route Claude makes while another is open keeps the open route's levels, so a light task cannot quietly replace a risky one. A workflow lighter than Susanoo recommends (`--profile fast|standard`) is refused unless your own message named it (for example `/susanoo --profile fast ...` or "use the fast workflow"), so Claude asks you instead; safety controls stay either way. For this, the hook keeps the keyword levels and signal names of your latest message per session, never its text.
- **It is hands-off by default.** Susanoo adds no approvals and no questions in any permission mode; Claude Code's own permission settings decide what needs confirmation. With `[enforcement] hands_off = false`, Claude Code's normal mode has at most one check-in per task (approving the plan, for STANDARD and DEEP only), and accept-edits, auto, dontAsk, or bypass mode still add nothing. Asking for autonomy in a task ("..., work autonomously", "don't ask", also mid-task) counts as approving its plan; only a request counts, not a feature description like "add a 'Don't ask again' checkbox". In every mode, Claude cannot finish while required checks lack passing evidence.
- **A playbook in `~/.claude/CLAUDE.md`** tells Claude how to follow the brief. A new file gets a "My rules" section that is yours to edit; Susanoo only ever rewrites its own workflow block between `<!-- susanoo:begin -->` and `<!-- susanoo:end -->`.
- **Records live in `~/.susanoo/projects/<repo>-<hash>/`**, so nothing is added to your repositories. Put an empty `.susanoo-off` file in a project to turn Susanoo off there. Global mode only applies inside git repositories, so the home directory, Downloads, or a drive root are never treated as projects. Routed prompts are stored after secret redaction and truncated; set `[records] store_task_text = false` in `~/.susanoo/config.toml` to keep only a hash.

`susanoo init --global` writes to your Claude Code configuration directory (`CLAUDE_CONFIG_DIR`, default `~/.claude`): the skill, the two subagents, Susanoo's hook entries in `settings.json` (your other settings and hooks are kept), and the `CLAUDE.md` block. Hooks call the installing Python by absolute path, so they work in every folder. `--no-auto` keeps the gates without automatic routing. `susanoo doctor` shows whether global mode is on for the current project. Restart running Claude Code sessions after installing.

A project that has its own `susanoo init` setup keeps using its `.susanoo/` folder for records and configuration. If it runs Susanoo hooks of its own (`--enforce-scope settings`), the global hooks step aside there, so nothing runs twice. A project that enforces through its skill (`susanoo init --enforce`) can get the gates twice after `/susanoo`; `susanoo doctor` warns and suggests `--enforce-scope settings` or `--no-enforce` for it. Each hook call starts a short Python process, so it adds a little time to every tool call and prompt in a repository: roughly half a second to a second on a typical machine, and one to two seconds to route a task (the first task of the day is slower while tool lookups are cached).

## Per project

```bash
pip install -e /path/to/susanoo     # or install into the environment Claude Code's shell uses
cd your-project
susanoo doctor
susanoo init                        # --dry-run to preview, --no-agents to skip subagents
```

`susanoo init` creates:

| File | Purpose |
|---|---|
| `.claude/skills/susanoo/SKILL.md` | The `/susanoo` skill |
| `.claude/agents/susanoo-reviewer.md` | Read-only independent reviewer subagent |
| `.claude/agents/susanoo-researcher.md` | Capability research subagent (web tools, never installs) |
| `.susanoo/config.toml` | Commented project configuration |
| `.susanoo/.gitignore` | Keeps `records/` and `install.json` out of git |

It does **not** modify `.claude/settings*.json`, `CLAUDE.md`, `.mcp.json`, or anything else you own. The one opt-in exception is `susanoo init --enforce --enforce-scope settings`, which adds Susanoo's own hook entries to `.claude/settings.local.json` (see [enforcement.md](enforcement.md#how-it-is-wired)). If a file already exists with different content, it is left alone and reported as a conflict. `--force` writes a backup before replacing. `susanoo init --uninstall` removes only files that still match what Susanoo installed.

Commit `.claude/skills/susanoo/`, `.claude/agents/susanoo-*.md`, and `.susanoo/config.toml` if you want the whole team to use them.

## Usage

```text
/susanoo Add rate limiting to the public API
/susanoo --profile fast Rename the config loader module
```

What happens:

1. Claude runs `susanoo route --stdin` with your task and receives the execution brief.
2. Claude summarizes the brief: mode, complexity, risk, uncertainty, mandatory controls, missing capabilities, steps, verification, and checkpoints. If confidence is low, it asks clarifying questions first.
3. **Checkpoints:** by default Claude shares the plan and keeps going, reporting checkpoints instead of stopping at them. With `[enforcement] hands_off = false` (the brief then says "Approval checkpoints"), Claude stops for your approval, including plan approval before editing in STANDARD and DEEP.
4. Claude executes the profile using its native tools. DEEP work uses plan mode, an isolated branch or worktree, and the `susanoo-reviewer` subagent for independent review.
5. Claude runs `susanoo verify --execute` and records the checks it performed itself with `susanoo evidence`.
6. Claude reports results, separating checks that ran from checks that did not, and records the outcome with `susanoo outcome`.

## Enforcement (optional)

`susanoo init --enforce` adds hooks to the skill so that verification evidence (and, with `hands_off = false`, plan approval and irreversible actions) is checked deterministically rather than left to instructions. By default the hooks apply in the Claude Code process where you ran `/susanoo`, so a session resumed later in a new process is not enforced; `--enforce-scope settings` puts them in your local settings instead, which covers resumed sessions. See [enforcement.md](enforcement.md) for this and the other limits. When you approve a plan (with `hands_off = false`), Claude records it with `susanoo approve`, which always asks for your confirmation.

## Permissions

The skill pre-approves only Susanoo's own read and record commands: `susanoo route`, `assess`, `research`, `evidence`, `outcome`, and `report`, plus `python -m susanoo route` as a fallback. None of them executes project code. The most `route --verify-tools` can do is run `--version` on packaged-registry tools found outside the project. `susanoo verify --execute` runs your project's test, lint, and type-check commands, so Claude Code asks before running it, as it would for any command that executes project code. To allow it without prompts, add a permission rule in your own settings. Susanoo will not do that for you.

## Which Claude Code features Susanoo relies on

| Feature | Used for |
|---|---|
| Skills (`.claude/skills/<name>/SKILL.md`, `$ARGUMENTS`, `allowed-tools`, `disable-model-invocation`) | the `/susanoo` entry point |
| Subagents (`.claude/agents/*.md`, `tools`, `model`) | independent review and capability research in separate contexts |
| Plan mode | the DEEP planning phase |
| Git worktrees (`claude --worktree`, subagent `isolation: worktree`) | isolated DEEP implementation |
| Permission system | approval of commands that run project code or install software |

Susanoo reads `.mcp.json`, `~/.claude.json`, settings files, and `.claude/` directories to build its inventory, and never writes them, except for its own hook entries with `--enforce-scope settings`.

## Without Claude Code

Every CLI command works standalone. `susanoo route` prints the same brief for a human, and `susanoo route --json` gives other tools the full plan.
