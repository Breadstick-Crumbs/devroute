# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
- **DevRoute is now Susanoo.** The project, Python package (`import susanoo`), command (`susanoo`), skill (`/susanoo`), subagents (`susanoo-reviewer`, `susanoo-researcher`), state directory (`.susanoo/`), hook commands (`susanoo hook ...`), frontmatter key (`susanoo-capabilities`), and environment variable (`SUSANOO_DEBUG`) were renamed. No `devroute` alias is kept.
- New record ids start with `su-`. Existing `dr-` records keep working.

### Added
- `susanoo init` migrates a DevRoute installation: it moves `.devroute/` to `.susanoo/` with records and configuration, keeps the enforce choice, and removes the old `/devroute` skill and `devroute-*` subagents only if they are unmodified. If `.susanoo/` already exists (a teammate committed the migration), local DevRoute records are merged into it. `susanoo init --uninstall` also removes the old files. `susanoo doctor` warns when a DevRoute installation is present, or when a settings file still runs `devroute hook` (the compaction snippet from `docs/enforcement.md`; change it to `susanoo hook`).
- Skills and subagents that declare capabilities with the v0.2 `devroute-capabilities` key are still recognized.

## [0.2.0] - 2026-10-13

### Added
- Opt-in workflow enforcement (`devroute init --enforce`): hooks registered by the `/devroute` skill add a plan-approval gate for edits, approval integrity, shell-write and irreversible-action confirmation, and an evidence gate on `Stop` with loop protection. Routes are bound to the Claude Code session that created them. Route re-injection after compaction is available through a settings snippet, because Claude Code does not fire compaction hooks declared in a skill. Sessions resumed in a new process are not enforced; see `docs/enforcement.md`.
- `scripts/live_enforcement_check.py`: a manual check of the gates against a real headless Claude Code session. Results for Claude Code 2.1.296 are in `docs/enforcement.md`.
- `devroute approve` and `devroute hook <event>` commands.
- `code_navigation` capability with repository-size-aware selection between built-in search and code-graph tools (MCP servers, skills, subagents), plus registry entries for code-graph MCP servers and ast-grep.
- `devroute-capabilities` frontmatter key so skills and subagents can declare their capabilities instead of relying on keyword inference.

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
- Verification Intelligence: risk-scaled verification plans, `devroute verify --execute` with machine-readable evidence, and separation of executed from reported results.
- Evaluation & Learning Engine: local JSONL records, per-profile summaries, sample-gated recommendations, and a deterministic routing evaluation suite (31 cases).
- CLI commands: `doctor`, `inventory`, `assess`, `route`, `init`, `report`, `verify`, `evidence`, `outcome`, `research`, `eval`.
- Claude Code integration: the `/devroute` skill and the `devroute-reviewer` and `devroute-researcher` subagents, with a manifest-based safe installer and uninstaller.
- Documentation, examples, and CI (lint, type check, tests on Linux, macOS, and Windows, security checks).
