# 0003: Integrate with Claude Code through a skill and subagents, not an MCP server

**Status:** accepted (v0.1)

## Context

DevRoute needs to be invocable from Claude Code as `/devroute <task>` and to guide Claude through the selected workflow.

## Options

1. A project skill (`.claude/skills/devroute/SKILL.md`) that calls the `devroute` CLI, plus subagent definitions.
2. An MCP server exposing assessment and routing tools.
3. A separate orchestrator that drives Claude Code programmatically.

## Decision

Option 1. A skill is Claude Code's native way to package a reusable workflow. With `disable-model-invocation: true` it adds no context until invoked. The CLI does the deterministic work and Claude Code's own subagents, plan mode, worktrees, and permission system do the rest. An MCP server would add tool definitions to every session and a long-running process without adding capability. A separate orchestrator would duplicate what Claude Code already does and make adoption harder.

## Consequences

- The CLI is the single source of behavior, usable without Claude Code.
- The skill guides Claude but cannot force it to follow every step. Approval checkpoints rely on Claude following instructions and on Claude Code's permission prompts.
- Packaging the skill and subagents as a Claude Code plugin is a natural later step (roadmap phase 6).
