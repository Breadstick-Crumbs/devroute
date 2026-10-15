# Architecture decision records

Short records of decisions that shape Susanoo, written when the decision was made. Each lists the options considered and why one was chosen. A record that adds a feature also says what the model and Claude Code already provide for it, and the exact gap Susanoo fills, so Susanoo builds checks and gates rather than what they already do. To revisit a decision, add a new record that supersedes the old one instead of editing it.

| ADR | Decision |
|---|---|
| [0001](0001-zero-runtime-dependencies.md) | Python standard library only at runtime |
| [0002](0002-deterministic-heuristic-routing.md) | Deterministic, configurable heuristics for assessment and routing |
| [0003](0003-skill-first-claude-code-integration.md) | Integrate with Claude Code through a skill and subagents, not an MCP server |
| [0004](0004-local-jsonl-records.md) | Local JSON Lines files for records |
| [0005](0005-safety-controls-independent-of-profile.md) | Safety controls derive from risk, not from the chosen profile |
| [0006](0006-model-judgment-above-a-keyword-floor.md) | With automatic routing, Claude decides where keywords are unsure, never below the keyword floor of the user's message |
| [0007](0007-hands-off-by-default.md) | Hands-off by default: Susanoo asks nothing; Claude Code's permissions decide; the evidence gate stays |
| [0008](0008-ui-ux-workflow.md) | UI work: require and record visual and accessibility checks, give domain checklists, never judge aesthetics (partly implemented) |
| [0009](0009-clarifying-questions-under-hands-off.md) | Hands-off covers permission prompts, not clarifying questions: Claude asks about the task when confidence is low |
| [0010](0010-taste-skill-and-screenshot-helper.md) | A `taste-ui` skill makes visual decisions from the user's taste profile (a quality bar, not a required look); `susanoo taste shoot` takes screenshots but Susanoo never judges them |
| [0011](0011-architecture-context.md) | STANDARD and DEEP briefs name the project's architecture documents, decision records, and boundary rules; its boundary tools become a recommended check |
| [0012](0012-delegation-plans.md) | Task decomposition and delegation plans: Claude drafts a tool-neutral JSON plan; Susanoo checks it and gives each unit a merge verdict from its own copy of the plan; Claude Code and Scion are optional executors (proposed) |
| [0013](0013-leases-and-landing-queue.md) | Leases, interface-change notices, and an evidence-gated local landing queue: the queue comes first and its records decide whether leases and notices are built (proposed) |
| [0014](0014-susanoo-land.md) | `susanoo land` tests agent branches on one combined tree, finds the branch that breaks it, and merges pull requests only with the user's `--merge` |
| [0015](0015-lead-session-and-board.md) | One lead session per repository hands each change to a worker through the app's own session features, has it reviewed with `/code-review` and landed with `susanoo land`; Susanoo adds only checks: evidence and reviews bound to a commit, a read-only `susanoo board`, and a merge gate (proposed) |
| [0016](0016-app-messages-are-not-the-users-message.md) | A prompt made only of the app's blocks (other sessions, notifications, status reminders) is not the user's message: it leaves routes and the recorded user message alone |
