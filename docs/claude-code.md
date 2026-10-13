# Using DevRoute with Claude Code

## Setup

```bash
pip install -e /path/to/devroute     # or install into the environment Claude Code's shell uses
cd your-project
devroute doctor
devroute init                        # --dry-run to preview, --no-agents to skip subagents
```

`devroute init` creates:

| File | Purpose |
|---|---|
| `.claude/skills/devroute/SKILL.md` | The `/devroute` skill |
| `.claude/agents/devroute-reviewer.md` | Read-only independent reviewer subagent |
| `.claude/agents/devroute-researcher.md` | Capability research subagent (web tools, never installs) |
| `.devroute/config.toml` | Commented project configuration |
| `.devroute/.gitignore` | Keeps `records/` and `install.json` out of git |

It does **not** modify `.claude/settings*.json`, `CLAUDE.md`, `.mcp.json`, or anything else you own. If a file already exists with different content, it is left alone and reported as a conflict. `--force` writes a backup before replacing. `devroute init --uninstall` removes only files that still match what DevRoute installed.

Commit `.claude/skills/devroute/`, `.claude/agents/devroute-*.md`, and `.devroute/config.toml` if you want the whole team to use them.

## Usage

```text
/devroute Add rate limiting to the public API
/devroute --profile fast Rename the config loader module
```

What happens:

1. Claude runs `devroute route --stdin` with your task and receives the execution brief.
2. Claude summarizes the brief: mode, complexity, risk, uncertainty, mandatory controls, missing capabilities, steps, verification, and checkpoints. If confidence is low, it asks clarifying questions first.
3. **Checkpoints:** FAST tasks with no checkpoints proceed directly. Otherwise Claude stops for your approval, including plan approval before editing in STANDARD and DEEP.
4. Claude executes the profile using its native tools. DEEP work uses plan mode, an isolated branch or worktree, and the `devroute-reviewer` subagent for independent review.
5. Claude runs `devroute verify --execute` and records the checks it performed itself with `devroute evidence`.
6. Claude reports results, separating checks that ran from checks that did not, and records the outcome with `devroute outcome`.

## Enforcement (optional)

`devroute init --enforce` adds hooks to the skill so that plan approval, verification evidence, and irreversible actions are checked deterministically rather than left to instructions. The hooks apply in the Claude Code process where you ran `/devroute`; a session resumed later in a new process is not enforced. See [enforcement.md](enforcement.md) for this and the other limits. When you approve a plan, Claude records it with `devroute approve`, which always asks for your confirmation.

## Permissions

The skill pre-approves only DevRoute's own read and record commands: `devroute route`, `assess`, `research`, `evidence`, `outcome`, and `report`, plus `python -m devroute route` as a fallback. None of them executes project code. The most `route --verify-tools` can do is run `--version` on packaged-registry tools found outside the project. `devroute verify --execute` runs your project's test, lint, and type-check commands, so Claude Code asks before running it, as it would for any command that executes project code. To allow it without prompts, add a permission rule in your own settings. DevRoute will not do that for you.

## Which Claude Code features DevRoute relies on

| Feature | Used for |
|---|---|
| Skills (`.claude/skills/<name>/SKILL.md`, `$ARGUMENTS`, `allowed-tools`, `disable-model-invocation`) | the `/devroute` entry point |
| Subagents (`.claude/agents/*.md`, `tools`, `model`) | independent review and capability research in separate contexts |
| Plan mode | the DEEP planning phase |
| Git worktrees (`claude --worktree`, subagent `isolation: worktree`) | isolated DEEP implementation |
| Permission system | approval of commands that run project code or install software |

DevRoute reads (never writes) `.mcp.json`, `~/.claude.json`, settings files, and `.claude/` directories to build its inventory.

## Without Claude Code

Every CLI command works standalone. `devroute route` prints the same brief for a human, and `devroute route --json` gives other tools the full plan.
