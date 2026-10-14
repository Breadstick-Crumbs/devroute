# Architecture decision records

Short records of decisions that shape Susanoo, written when the decision was made. Each lists the options considered and why one was chosen. To revisit a decision, add a new record that supersedes the old one instead of editing it.

| ADR | Decision |
|---|---|
| [0001](0001-zero-runtime-dependencies.md) | Python standard library only at runtime |
| [0002](0002-deterministic-heuristic-routing.md) | Deterministic, configurable heuristics for assessment and routing |
| [0003](0003-skill-first-claude-code-integration.md) | Integrate with Claude Code through a skill and subagents, not an MCP server |
| [0004](0004-local-jsonl-records.md) | Local JSON Lines files for records |
| [0005](0005-safety-controls-independent-of-profile.md) | Safety controls derive from risk, not from the chosen profile |
| [0006](0006-model-judgment-above-a-keyword-floor.md) | With automatic routing, Claude decides where keywords are unsure, never below the keyword floor of the user's message |
| [0007](0007-hands-off-by-default.md) | Hands-off by default: Susanoo asks nothing; Claude Code's permissions decide; the evidence gate stays |
| [0008](0008-ui-ux-workflow.md) | UI work: require and record visual and accessibility checks, give domain checklists, never judge aesthetics (proposed) |
| [0009](0009-clarifying-questions-under-hands-off.md) | Hands-off covers permission prompts, not clarifying questions: Claude asks about the task when confidence is low |
