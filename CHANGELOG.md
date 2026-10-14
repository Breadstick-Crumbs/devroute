# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Breaking: irreversible commands no longer ask by default, also in Claude Code's normal mode.** With `--enforce`, the irreversible-action confirmation (force push, history rewrite, deploy and publish, `terraform destroy`, `kubectl delete`, destructive SQL) used to ask in normal and plan mode; with the new default `hands_off = true` it does not, so a command you allowlisted in Claude Code runs without any confirmation. Existing `--enforce` installs get this on upgrade. Set `[enforcement] hands_off = false` to bring it back. See ADR 0007.
- **Hands-off by default:** `[enforcement] hands_off = true` turns off Susanoo's own approvals and confirmations (plan approval, shell writes before approval, irreversible actions, route integrity) in every Claude Code permission mode; Claude Code's permission settings decide what needs confirmation. The evidence gate stays, `susanoo approve` still asks (an approval Claude recorded itself would count later), and `--accept-missing-evidence` is refused instead of asked about. Briefs title the section "Checkpoints" and mark phases `[checkpoint]`, and the skill and playbook report checkpoints instead of stopping at them. Set `hands_off = false` for the previous behavior.
- The package.json test script runs as `<manager> run test` (was `npm test`) and is listed as "package.json test script".

### Added
- **Claude's judgment above a keyword floor** (ADR 0006). With automatic routing, prompts the hook does not route get a one-line note, and Claude routes them itself when they need code changes ("the checkout page is broken"). `susanoo route` in such a session combines Claude's description with the keyword assessment of the user's latest message: every dimension takes the stricter level, the message's signals count, and the profile is never lower than the message alone. A route that replaces an open one keeps that route's levels too, after any kind of message; `susanoo route` never replaces a route with unfinished work. `--profile` changes only the profile and never removes a safety control. `/susanoo` keeps its full brief. The hook keeps only levels and signal ids per session (from the first 20,000 characters, without counting vagueness as risk), never the message text.
- `susanoo raise standard|deep --reason "<the risk>"` makes the open route stricter. It never lowers a route or drops a control: the open route is its floor, the target is a minimum, and risk named in the reason turns on the matching controls (also on a route that is already DEEP). Recorded evidence carries over, and so does autonomy asked for in words; a plan approval does not, because the plan changed. Every brief mentions it.
- With automatic routing, `susanoo route --profile fast|standard` refuses a profile below Susanoo's recommendation unless the user's latest message named it, so Claude asks the user instead. In such a session, `route` and `raise` refuse `--config`, and every route keeps the open route's controls even if the prompt record is lost. `route` no longer accepts abbreviated options.
- **Route before edit:** with automatic routing, when a message was not routed and Claude edits a project file with no route open, that first edit is denied once and Claude is told to run `susanoo route` first; a retry goes through. It never asks the user, so it applies in every permission mode. `[auto] route_before_edit = false` turns it off, and agent memory and settings (`CLAUDE.md`, `AGENTS.md`, `.claude/`) are exempt (`route_before_edit_exempt`). Edits through shell commands are not covered.
- `scripts/live_auto_check.py --only unrouted`: live check that Claude routes a problem report the hook does not route.
- Briefs name installed skills, subagents, and MCP servers that fit each capability, one of each kind first ("also installed: ..."), including the short brief that automatic routing adds; previously only the top-ranked tool was shown, so a configured code-graph skill or MCP server went unmentioned.
- JavaScript package managers: the project's manager (package.json `packageManager`, else its lockfile) decides how commands are spelled. The test script runs as `pnpm run test`, `bun run test`, `yarn run test`, or `npm run test`; install hints use `pnpm add -D`, `bun add -d`, and so on; `[selection] default_package_manager` covers projects without either. New `pnpm audit` and `bun audit` registry entries.
- Task keywords in the tool registry: a tool the task names, or whose technology it names, is preferred among comparable options ("postgres" suggests psql, "sqlite" sqlite3). `[selection] task_keyword_bonus`.
- Gap suggestions include the install hint ("consider Gitleaks (cli), install: ...").
- `dependency-change` and `dependency-upgrade-broad` signals: adding, removing, or bumping packages recommends a dependency audit; upgrading many packages or major versions also raises uncertainty and testing difficulty. Verification `escalate` entries can name a `signal`. "install", "uninstall", "pin", and "unpin" start a task in automatic routing ("install docker" and "install the project dependencies" do not). Six routing-suite cases for dependency changes, labeled before the signals were written or tuned ("Upgrade django from 3 to 5" is a major-version jump, which counts as a broad upgrade); one ("Replace moment with date-fns across the codebase") still routes FAST instead of STANDARD, because keywords cannot tell that `moment` is a library.

### Fixed
- `susanoo inventory` reports "GitHub remote configured" only when a remote's host is `github.com` (or a subdomain). It used to match `github.com` anywhere in the URL, so remotes such as `https://github.com.example.org/...` were counted (CodeQL `py/incomplete-url-substring-sanitization`).
- Python checks in Poetry and Pipenv projects no longer run with the wrong interpreter. These managers keep the virtualenv outside the project by default, so `pytest`, `ruff`, `mypy` and other Python tools were planned (and, with `verify --execute`, run) from PATH without the project's dependencies, and the result was recorded as executed evidence. When such a project has no in-project environment (`.venv`, `venv`, `env`) and no `uv.lock`, Python tools and scripts (including the Django migration check) now run through `poetry run` / `pipenv run`, and tools declared in the project's dependencies (`pyproject.toml`, requirements files, and now `Pipfile` `[packages]` / `[dev-packages]`) count as installed even though they are not on PATH. If the manager is not on PATH, those checks are not run by Susanoo: a tool found on PATH becomes a manual step, a tool found nowhere stays a gap, and either way the reason is shown in the brief, `susanoo verify` and the skipped evidence note (and, for manual steps, the auto-mode brief); a runnable alternative such as `make test` is preferred. Managers are data (`[[environments]]` in the packaged `tools.toml`); `susanoo inventory` lists the detected one. Wrapped commands need the Poetry or Pipenv environment: `poetry run` / `pipenv run` may create it during `verify --execute`. The managers resolve the tool inside their environment first; a tool that is not installed there still falls back to PATH.
- `npm audit` was selected in pnpm and Bun projects whenever npm was on PATH, and failed there without a package-lock.json; it now applies only to npm projects.
- `bun.lock` (Bun 1.2 and later) is recognized as a Bun project.

## [0.6.0] - 2026-10-13

### Added
- **Global auto mode** (#34): `susanoo init --global` makes Susanoo the default workflow in every project. Coding prompts are routed automatically by a `UserPromptSubmit` hook (questions and ambiguous prompts pass through; a leading `?` skips routing; follow-ups continue the current task, and an unrelated or stricter task closes it and starts its own), a playbook goes into `~/.claude/CLAUDE.md` (a rules section written once, plus a managed workflow block), and records live in `~/.susanoo/projects/` so nothing is added to repositories. `.susanoo-off` turns it off in one project. Global mode applies only inside git repositories, and the global hooks step aside in projects that run Susanoo hooks of their own (`--enforce-scope settings`); `susanoo doctor` warns about skill-scope projects, where gates could run twice. `--no-auto` keeps the gates without routing; `--uninstall` removes what was added. Hooks run with `python -P` so project files are never imported, and a fast entry point keeps them light.
- **Autonomy-aware gates:** in Claude Code's acceptEdits, auto, dontAsk, and bypassPermissions modes, Susanoo adds no approvals or questions (`[enforcement] autonomous_modes`), and refuses `--accept-missing-evidence` instead of asking. Asking for autonomy in a task ("work autonomously", "don't ask", also mid-task) counts as approving its plan; the irreversible-action confirmation stays. The evidence gate stays in every mode.
- `--auto` for project installs with the settings scope; `~/.susanoo/config.toml` for user-wide configuration; `SUSANOO_HOME`; `susanoo doctor` reports global mode.
- `scripts/live_auto_check.py`: live checks of automatic routing; results in `docs/enforcement.md`.
- Visual identity: an original armored-helmet (kabuto) mark with a lightning crest, Cascadia Code wordmark, light and dark README banners, and a social preview card, all generated by `scripts/make_brand_assets.py` (the card's terminal lines come from real routing results).
- README redesign: an animated terminal demo generated from real `susanoo route` output (`scripts/make_demo_svg.py`, checked by a test), a workflow diagram, a comparison with plain Claude Code, and an FAQ.
- `llms.txt` (a plain summary for AI search and assistants) and `CITATION.cff` (GitHub's "Cite this repository").

### Changed
- In the default autonomous modes, the plan-approval gate, approval confirmation, and the shell-write, irreversible-action, and route-integrity asks no longer apply. Set `autonomous_modes = []` for the previous behavior.

## [0.5.0] - 2026-10-13

### Added
- Negation in task assessment (#11): an instruction to leave a code location alone ("fix the footer typo without touching the payments module", "rename the helper without changing the database schema") no longer counts as signals, and is listed as "Ignored as out of scope". It is narrow on purpose: only instructions (not descriptions such as "the job is not changing the hash"), only with a code-location noun, ending at that noun or at a sentence or line end, never across destructive-data or production phrases, and only when the rest of the task is a single clause with a clearly trivial edit keyword (typo, wording, rename, docstring, ...) and no new functionality. "Without breaking payments" and "without touching the payment code, add idempotency keys" still count. Configurable in `[assessment.negation]`.
- Test-only tasks (#11): a short, plain test request ("write unit tests for the invoice total calculator") gets security and data-integrity risk capped at low. A longer subject, a second clause, prepositions ("by", "in", "using", ...), "-ing" words, words such as production, deploy, real, or live, and a database, destructive-data, or production signal all keep the full risk. Irreversibility and external-dependency risk are never capped, so production and sandbox controls stay. Generic `caps_only_if` and `caps_unless_signals` support for signals, and a `trivial-edit` signal (no effects) that negation requires.
- A property test checks 75 adversarial wordings from four rounds of independent review: the new logic must not lower their risk.
- The sensitive-data signal also matches "SSNs".
- Eight routing-suite cases for negation and test-only scope, committed before the heuristics changed.

### Changed
- The destructive-data signal also matches "delete all user records", "deleting ... records", and "migrate the data".
- The suite case `tests-only-payments` gained `min_risk = "moderate"` after review, so test-only caps cannot silently remove external-service risk.
- The small-change rename pattern also matches a name before the noun and `helper` / `constant` ("rename the getUser helper"). This was adjusted after seeing a new suite case route STANDARD.
- Routing suite: 97.4% agreement (38/39), 0 risk-floor violations; the v0.1 test-only mismatch is fixed.

## [0.4.0] - 2026-10-13

### Added
- `susanoo init --enforce --enforce-scope settings` puts the enforcement hooks in `.claude/settings.local.json` instead of the skill, so the gates also apply in sessions resumed with `claude --resume` or `--continue`, and compaction recovery works without a manual snippet. Only Susanoo's own hook entries are added or removed and the rest of the file is kept; a file that is not valid JSON, has an unexpected shape, or is a symbolic link stops `init` before anything changes. Because Claude Code does not deduplicate identical hooks, `init` refuses any result that would put the gates in two places (for example a modified skill that still has hooks), and `susanoo doctor` reports where the hooks are and warns if it finds them in more than one place. Switching back to the skill scope keeps the compaction hook. The default scope stays `skill`, and the choice is kept for later `init` runs.
- `scripts/live_enforcement_check.py --scope settings` runs the live checks with settings-scoped hooks.

## [0.3.0] - 2026-10-13

### Changed
- Renamed to Susanoo: the Python package (`import susanoo`), command, skill (`/susanoo`), subagents, state directory (`.susanoo/`), hook commands, frontmatter key (`susanoo-capabilities`), and environment variable (`SUSANOO_DEBUG`). New record ids start with `su-`.

## [0.2.0] - 2026-10-13

### Added
- Opt-in workflow enforcement (`susanoo init --enforce`): hooks registered by the `/susanoo` skill add a plan-approval gate for edits, approval integrity, shell-write and irreversible-action confirmation, and an evidence gate on `Stop` with loop protection. Routes are bound to the Claude Code session that created them. Route re-injection after compaction is available through a settings snippet, because Claude Code does not fire compaction hooks declared in a skill. Sessions resumed in a new process are not enforced; see `docs/enforcement.md`.
- `scripts/live_enforcement_check.py`: a manual check of the gates against a real headless Claude Code session. Results for Claude Code 2.1.296 are in `docs/enforcement.md`.
- `susanoo approve` and `susanoo hook <event>` commands.
- `code_navigation` capability with repository-size-aware selection between built-in search and code-graph tools (MCP servers, skills, subagents), plus registry entries for code-graph MCP servers and ast-grep.
- `susanoo-capabilities` frontmatter key so skills and subagents can declare their capabilities instead of relying on keyword inference.

### Changed
- An agent can no longer record an outcome as `completed` while required verification lacks passing evidence (unless `--accept-missing-evidence`, which enforcement mode always confirms with the user).
- Capability inference no longer treats generic words like "audit" as code review.

## [0.1.0] - 2026-10-13

First public release: a working vertical slice of the framework.

### Added
- Task Intelligence Engine: ten-dimension explainable assessment with independent complexity, risk, and uncertainty composites, confidence reporting, and project-context adjustments.
- Adaptive Workflow Router: FAST, STANDARD, and DEEP profiles; profile overrides that keep mandatory safety controls; a configurable critical-risk floor.
- Capability Intelligence Engine: read-only discovery of languages, manifests, tools, Claude Code skills, commands, subagents, MCP servers, and plugins with five-state statuses; opt-in version verification.
- Tool Selection Engine with a 46-entry registry and a preference for simple, low-context integrations.
- Capability Research Engine: gap analysis, the `ResearchProvider` interface, and a local registry provider with security notes.
- Verification Intelligence: risk-scaled verification plans, `susanoo verify --execute` with machine-readable evidence, and separation of executed from reported results.
- Evaluation & Learning Engine: local JSONL records, per-profile summaries, sample-gated recommendations, and a deterministic routing evaluation suite (31 cases).
- CLI commands: `doctor`, `inventory`, `assess`, `route`, `init`, `report`, `verify`, `evidence`, `outcome`, `research`, `eval`.
- Claude Code integration: the `/susanoo` skill and the `susanoo-reviewer` and `susanoo-researcher` subagents, with a manifest-based safe installer and uninstaller.
- Documentation, examples, and CI (lint, type check, tests on Linux, macOS, and Windows, security checks).
