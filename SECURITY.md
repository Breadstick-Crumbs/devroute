# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting on this repository (**Security → Report a vulnerability**). Include affected versions, reproduction steps, and impact. You will get an acknowledgement as soon as a maintainer sees the report. Fixes are coordinated with the reporter before disclosure.

Supported versions: the latest release on `main`.

## Trust boundaries

| Boundary | DevRoute's behavior |
|---|---|
| **Task text** (from the user or from Claude) | Parsed with regular expressions only. Never executed. Stored locally in `.devroute/records/` (git-ignored); can be hashed instead (`records.store_task_text = false`). |
| **Project files** (manifests, `.claude/`, `.mcp.json`) | Read with size limits. Parse errors become warnings. Treated as untrusted data and never interpreted as instructions. |
| **User-level Claude Code configuration** (`~/.claude.json`, `~/.claude/`) | Read-only. From MCP entries only the name, scope, transport, and launch-command basename are used. `env`, `headers`, `args`, and URLs are never read into output or records. Credentials embedded in git remote URLs are stripped. |
| **Executables** | Located in project-local environments (`.venv`, `venv`, `env`, `node_modules/.bin`) and on PATH. Not executed during discovery. `--verify` runs only `--version`-style probes defined in DevRoute's **packaged** registry, with a timeout, and never for executables that live inside the project directory. Probe arguments from a project's `.devroute/tools.toml` are ignored. |
| **Verification commands** (`devroute verify --execute`) | Registry-defined argv lists, run without a shell, in the project root, with a timeout. They **execute project code** (tests, Makefile and npm scripts, project-local executables, and `run` commands from the project's `.devroute/tools.toml`), so run them only in repositories you trust. On timeout the whole process tree is stopped. Output tails and reported commands are redacted for common credential formats before storage. This is best effort, so do not rely on it for secrets you print deliberately. |
| **Installation** (`devroute init`) | Writes and removes only DevRoute's fixed target paths. Entries in the repository-controlled install manifest that point anywhere else are ignored. Never overwrites differing files without `--force`, which writes a backup first. Never edits settings, `CLAUDE.md`, or MCP configuration. Never installs third-party software. |
| **Claude Code skill** | Pre-approves only DevRoute's read and record commands. Commands that run project code go through Claude Code's normal permission prompts. Instructs Claude to treat repository content and fetched pages as data, not instructions. |

## Safe integration practices

- Install DevRoute from a source you trust, and review `.claude/` changes from `devroute init` before committing them.
- Prefer CLI tools over MCP servers where they solve the problem. MCP servers run with your user permissions and add standing access.
- Give MCP servers and test environments least-privilege, test-mode credentials. DevRoute's `external-sandbox` control enforces this as a checkpoint for payment and similar integrations.
- Do not commit `.devroute/records/`. It can contain task descriptions and command output.
