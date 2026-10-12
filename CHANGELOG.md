# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-13

### Added
- Opt-in workflow enforcement (`devroute init --enforce`): hooks registered by the `/devroute` skill add a plan-approval gate for edits, approval integrity, shell-write and irreversible-action confirmation, an evidence gate on `Stop` with loop protection, and route re-injection after compaction. Routes are bound to the Claude Code session that created them.
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
