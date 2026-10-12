# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
