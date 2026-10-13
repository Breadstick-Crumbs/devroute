# Susanoo

**The right workflow for every development task.**

*Formerly DevRoute. Named after Susanoo, the storm god of Japanese myth who slew the eight-headed serpent Orochi.*

Susanoo is an adaptive engineering framework for AI-assisted development in [Claude Code](https://code.claude.com/docs). You describe a task; Susanoo assesses how complex and risky it is, checks what your environment can actually do, and recommends a workflow sized to the job: a quick fix for a typo, a planned and reviewed change for a feature, and an architecture-first, independently reviewed, evidence-backed process for work that can hurt you.

[![CI](https://github.com/Breadstick-Crumbs/susanoo/actions/workflows/ci.yml/badge.svg)](https://github.com/Breadstick-Crumbs/susanoo/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Status](https://img.shields.io/badge/status-alpha-orange)

```text
/susanoo Build a SaaS dashboard with authentication and billing

Recommended mode: DEEP   (risk is critical; complexity is high)
Mandatory controls: security-review, data-safety, external-sandbox
Missing: payment sandbox (blocking), secret scanning (blocking)
Steps: requirements & architecture [approval] -> technical plan [approval] -> isolated implementation
       -> independent review -> integration & security verification -> evidence-backed completion
```

## Why

AI coding agents tend to apply the same process to everything. That wastes time and context on trivial changes and under-protects risky ones. A one-line change to password hashing is small but dangerous. A new dashboard page is larger but low-risk. Treating them the same is a mistake in both directions.

Susanoo keeps **complexity and risk as separate dimensions**, so a small change with security implications is never treated as routine. It also checks the tools that are actually installed instead of assuming they exist. It treats verification as **evidence that a command ran and passed**, not as a completion message.

## What it does today

| Component | What it does |
|---|---|
| **Task Intelligence** | Scores 10 dimensions (implementation, architecture, components, uncertainty, external dependencies, security/privacy, data integrity, irreversibility, testing difficulty, parallelization) from configurable, explainable heuristics. Complexity, risk, and uncertainty are independent composites. Low-information tasks are flagged as low-confidence instead of guessed. |
| **Adaptive Router** | Chooses FAST, STANDARD, or DEEP with stated reasons. You can override the profile. Mandatory safety controls (security review, data safety, irreversible-action gate, sandbox credentials) still apply after an override, and critical-risk tasks cannot be routed below a configurable floor. |
| **Capability Intelligence** | Read-only discovery of languages, build systems, test frameworks, linters, type checkers, security tools, Claude Code skills, subagents, slash commands, MCP servers, and plugins. Every finding is classified as *verified*, *installed but unverified*, *configured but inaccessible*, *missing*, or *not required*. It never reads MCP credentials. |
| **Tool Selection** | Picks the simplest reliable tool for each needed capability. A working tool beats a better one that isn't installed. CLIs and native tools are preferred over MCP servers, which add context and standing access. |
| **Capability Research** | Identifies gaps, marks which ones block the task, and evaluates candidates from a curated registry with security notes and validation steps. A `susanoo-researcher` subagent handles manual web research. Nothing is ever installed automatically. |
| **Verification Intelligence** | Selects checks by profile and risk, runs the ready ones with `susanoo verify --execute`, and stores machine-readable evidence. Results Susanoo executed itself are kept separate from results an agent reported. |
| **Evaluation & Learning** | Local JSONL records of routes, outcomes (completion, time, interventions, tokens, cost, regressions), and evidence. Per-profile summaries. Recommendations for human review, only with enough data. A deterministic routing evaluation suite. |
| **Claude Code integration** | A `/susanoo` project skill and two subagents (`susanoo-reviewer`, `susanoo-researcher`), installed without touching your settings, `CLAUDE.md`, or MCP configuration. |
| **Workflow enforcement (opt-in)** | `susanoo init --enforce` adds hooks to the skill that turn the critical steps into hard checks. File edits wait for your plan approval, Claude cannot approve its own plan, irreversible commands need confirmation, and the turn cannot end without verification evidence. Only sessions that use `/susanoo` are affected, and resumed sessions are not enforced. See [docs/enforcement.md](docs/enforcement.md). |
| **Code navigation** | Size-aware choice between built-in search and code-graph tools (for example a codebase-memory MCP server or its skills and agents). In large repositories the investigation phase names the graph tool. |

## Install

Requires Python 3.11+ and no runtime dependencies.

```bash
git clone https://github.com/Breadstick-Crumbs/susanoo.git
cd susanoo
pip install -e .
```

Then, in the project you want to work on:

```bash
susanoo doctor   # check the environment
susanoo init     # add the /susanoo skill, subagents, and .susanoo/ (use --dry-run to preview)
                 # add --enforce to turn checkpoints and verification into hook-enforced gates
```

`susanoo init` never overwrites files it did not create. It reports conflicts, `--force` writes a backup before replacing, and `susanoo init --uninstall` removes only files that are still unmodified.

**Upgrading from DevRoute:** uninstall the old package (`pip uninstall devroute`), install Susanoo, and run `susanoo init` in each project. It moves `.devroute/` (records and configuration) to `.susanoo/`, keeps your enforcement choice, and replaces the `/devroute` skill and `devroute-*` subagents if you have not modified them. Modified files are left in place and listed. In a team, commit the result once; teammates who pull it and run `susanoo init` get their local records merged into `.susanoo/`. If you added the compaction hook to your settings, change `devroute hook` to `susanoo hook` there; `susanoo doctor` points out any you missed.

## Quick start

### In Claude Code

```text
/susanoo Add a low-stock alert that emails the warehouse when a SKU drops below its reorder level
```

Claude runs Susanoo and presents the execution brief. For STANDARD and DEEP, it waits for your approval at each checkpoint. It then works through the chosen workflow, runs the required verification, records evidence, and reports what ran and what didn't. To force a workflow, use `/susanoo --profile fast <task>`. Safety controls still apply.

### On the command line

The CLI works without Claude Code and without a model:

```bash
susanoo assess "Change password hashing from SHA1 to bcrypt"      # complexity / risk / uncertainty
susanoo route  "Add pagination to the /users API endpoint"         # full execution brief
susanoo route  --json "..."                                         # machine-readable
susanoo inventory --verify                                          # what this environment can do
susanoo verify --execute                                            # run the ready checks for the latest route
susanoo research browser_testing                                    # options for a capability gap
susanoo outcome latest --status completed --minutes 25              # record how it went
susanoo report                                                      # records, summaries, suggestions
susanoo eval                                                        # routing behavior on the reference suite
```

## Example

From [`examples/02-moderate.md`](examples/02-moderate.md), run against the bundled [`sample-app`](examples/sample-app):

```text
$ susanoo route --path examples/sample-app "Add a low-stock alert that emails the warehouse when a SKU drops below its reorder level"

## Assessment
- Complexity:  MODERATE (0.32) - implementation_complexity=moderate
- Risk:        MODERATE (0.54) - external_dependencies=moderate
- Uncertainty: LOW (0.00)
- Confidence:  high

## Recommended mode: STANDARD
Why: complexity is moderate; risk is moderate

## Required verification
- [required] Unit tests: `pytest -q`
- [required] Linting / static analysis: `ruff check .`
- [required] Integration tests: `pytest -q`
- [recommended] Type checking: `mypy .`
- [recommended] Dependency vulnerability audit: no usable tool - gap
- [recommended] Independent code review: Claude Code subagents (performed by the agent or a person)

## Approval checkpoints
- Approve the implementation plan before files are changed.
```

See [`examples/`](examples/) for a simple (FAST), moderate (STANDARD), and complex (DEEP) brief in full.

## How it works

```text
task text ──► Task Intelligence ──► Adaptive Router ──► profile + mandatory controls
                    ▲                                          │
project files ──────┤                                          ▼
                    │                       required capabilities (task + profile + verification)
Capability ─────────┴─► inventory ──► Tool Selection ──► tools, gaps ──► Capability Research
Intelligence                                   │
                                               ▼
                               Verification plan ──► susanoo verify --execute ──► evidence
                                                                                      │
                     Claude Code (/susanoo skill, subagents) ◄── execution brief      ▼
                                                                         local records ──► report / eval
```

The flow is deterministic: the same task, configuration, and environment always produce the same brief. The model is not part of the routing. Claude Code does the engineering, and Susanoo decides how much process the task deserves and checks the evidence. See [docs/architecture/overview.md](docs/architecture/overview.md).

## Configuration

Every heuristic lives in [`src/susanoo/data/defaults.toml`](src/susanoo/data/defaults.toml) and can be overridden per project in `.susanoo/config.toml`: signal patterns and effects, dimension weights, thresholds, safety controls, verification rules, and the critical-risk floor. You can add tools in `.susanoo/tools.toml` and adjust profiles in `.susanoo/profiles/<name>.toml`. See [docs/configuration.md](docs/configuration.md).

## Limitations

Susanoo is an early, alpha-stage project. Know where its edges are:

- **Heuristic assessment.** Scores come from keyword signals in the task description plus a few project facts. They are hand-tuned starting points, **not validated measurements**. Negation ("without touching the database") is not understood. A test-only task in a sensitive domain is treated conservatively (see the known mismatch in `susanoo eval`).
- **Routing evaluation is not outcome evaluation.** The bundled suite measures agreement with maintainer expectations, and the heuristics and labels were developed together. No claim is made that a profile produces better software until outcome data exists. See [docs/evaluation.md](docs/evaluation.md).
- **Discovery is mostly read-only.** "Installed" means found, not exercised. `--verify` runs `--version` checks only. MCP servers are never started and remote servers are never contacted.
- **Research is local.** Candidate tools come from a curated registry. There is no autonomous internet-wide discovery. Use the `susanoo-researcher` subagent for manual research.
- **Learning is advisory.** `susanoo report` suggests changes for you to review. Nothing adjusts configuration automatically.
- **Claude Code integration is instruction-based unless enforcement is on.** Without `--enforce`, the skill guides Claude but cannot force every step. With it, plan approval, evidence, and irreversible actions are checked by hooks. Those are guardrails against drift, not a sandbox. The main gates pass single live runs on Claude Code 2.1.296, but sessions resumed in a new process are not enforced and compaction recovery needs a settings snippet (see [docs/enforcement.md](docs/enforcement.md#limits)).

## Roadmap

| Phase | Focus | Status |
|---|---|---|
| 1. Core framework | Assessment, routing, CLI, Claude Code skill, tests | Done (v0.1) |
| 2. Capability intelligence | Deeper discovery, MCP/CLI selection, integration validation | Partially in v0.1 |
| 3. Adaptive execution | Task decomposition, specialist delegation, stronger verification | In progress (enforcement in v0.2) |
| 4. Measurement | Benchmarks, cost analysis, workflow experiments | Infrastructure in v0.1 |
| 5. Advanced intelligence | Context optimization, simulation, model routing, learning from verified outcomes | Research |
| 6. Ecosystem | Community integrations, plugin distribution, multi-harness adapters | Planned |

Details and acceptance criteria: [docs/roadmap.md](docs/roadmap.md). Work is tracked in [issues and milestones](https://github.com/Breadstick-Crumbs/susanoo/milestones).

## Contributing

Contributions are welcome. The most useful ones right now are new tool registry entries, better signals backed by routing-suite cases, and real-world outcome reports. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
