# Architecture overview

Susanoo is a deterministic Python library with a CLI and a Claude Code adapter. It decides **how much engineering process a task deserves** and **what evidence proves it is done**. The engineering itself is done by Claude Code (or a person).

## Design principles

1. **Right-sized process.** More agents, tools, and steps are not automatically better. Each profile adds process only where the assessment justifies it.
2. **Complexity and risk are independent.** A small change can be dangerous, and a large one can be safe. Risk is dominated by its worst dimension so it cannot be averaged away.
3. **Explainability over cleverness.** Every score lists the signals and context that produced it. The heuristics are plain TOML.
4. **Determinism.** Susanoo makes no model calls: assessment, routing, selection, and verification planning are rules. The same inputs produce the same brief, which makes behavior testable and evaluable. With automatic routing, Claude may route a message itself or raise a route, but the result never goes below the keyword floor of the user's message ([ADR 0006](../decisions/0006-model-judgment-above-a-keyword-floor.md)).
5. **Read-only by default.** Discovery reads files and looks up executables. It runs nothing unless you pass `--verify` (version probes) or `verify --execute` (project checks).
6. **Evidence, not assertions.** Verification results record who produced them (`executed` by Susanoo or `reported` by an agent or person), plus the command, exit code, and duration.
7. **Never silently change trusted configuration.** The installer writes only Susanoo-owned files. Learning produces suggestions, not edits.

## Components

```text
src/susanoo/
  analyzer/            Task Intelligence Engine        assess(task, config, context, floor) -> Assessment
  router.py            Adaptive Workflow Router         route(assessment, profiles, config, override, minimum) -> RouteDecision
  discovery/           Capability Intelligence Engine   build_inventory(root, registry) -> Inventory
  registry.py          Tool & capability catalog        load_registry(root) -> Registry
  selection.py         Tool Selection Engine            recommend(requirements, inventory, registry)
  research.py          Capability Research Engine       analyze_gaps(...), research(capability, ...)
  verification/        Verification Intelligence        select_steps / build_plan / execute_plan
  evaluation/          Evaluation & Learning Engine     RecordStore, summarize, run_suite
  enforcement/         Opt-in workflow enforcement      gates (pure decisions), hooks (Claude Code bridge)
  adapters/claude_code Claude Code integration          discovery of skills/agents/MCP, installer
  pipeline.py          Orchestration                    plan_task(task, root, config) -> TaskPlan
  render.py            Execution brief and reports
  cli.py               Command-line interface
  data/                defaults.toml, profiles/, tools.toml, capabilities.toml, suites/, templates/
```

### Task Intelligence Engine (`analyzer/`)

- **Signals** (`[[signals]]` in `defaults.toml`) are regular expressions matched on word boundaries. Each signal raises one or more of ten dimensions to a level from 0 to 3, may mark a *feature area*, and may require or recommend capabilities.
- **Dimension scoring.** A dimension's level is the maximum contribution. When two or more independent signals push the same dimension to level 2 or higher, it rises one more step. The number of distinct feature areas drives `affected_components` and `parallelization`. Small-change signals cap complexity dimensions unless a scope signal is present.
- **Floor.** When Claude routes a task in its own words (automatic routing only), the keyword assessment of the user's message (and any open route it replaces, or a route being raised) is passed as a floor: after caps and before project context, every dimension takes the stricter level and the message's signals and areas are added, so the result never goes below the message alone.
- **Project context** adjusts a few dimensions and always says so in the output: no test framework, not a git repository, a very large repository.
- **Composites:**
  - *complexity* is a weighted mean of implementation, architecture, components, and testing difficulty;
  - *risk* is `max_weight * max + (1 - max_weight) * mean` over security, data integrity, irreversibility, and external dependencies, and becomes critical when two risk dimensions are at the highest level;
  - *uncertainty* is technical uncertainty plus a penalty when the description lacks enough information for a confident assessment.
- **Confidence** is `low` when the description is very short or no signals match. The router then adds a scope-confirmation checkpoint.

### Adaptive Workflow Router (`router.py`)

Rules fire independently and the strictest profile wins:

| Rule | Profile |
|---|---|
| risk is critical, or complexity is high, or uncertainty is high on a non-trivial change | DEEP |
| risk is high, or complexity / risk / uncertainty is moderate, or uncertainty is high | STANDARD |
| everything low | FAST |

**Mandatory controls** (`[[controls]]`) are derived from dimension levels, not from the profile, so overriding the profile cannot remove them. Each control adds an approval checkpoint and forces specific verification steps to *required*. When risk is critical, overrides below `routing.critical_risk_floor` (default STANDARD) are raised to the floor.

Profiles live in `data/profiles/*.toml`: phases (with approval flags), planning depth, delegation, review, isolation, verification depth, and baseline capabilities.

### Capability Intelligence Engine (`discovery/`, `adapters/claude_code/discovery.py`)

| Source | How it is read | Status rules |
|---|---|---|
| Source files | bounded directory walk that skips dependency and build directories | languages are counted |
| Manifests | `pyproject.toml`, `requirements*.txt`, `package.json`, `Makefile`, `.git/config` | declarations make a tool *configured* |
| Executables | project `.venv`/`venv`/`node_modules/.bin`, then `PATH`, then the interpreter directory | found means *installed (unverified)*; a successful `--verify` version probe means *verified* |
| Python environment managers | `[[environments]]` in the packaged `tools.toml` (Poetry, Pipenv): marker files, with no in-project environment | Python tools run through the manager's `run` prefix, and tools declared in the project's dependencies count as installed; without the manager on PATH their checks become manual, so a PATH interpreter's result is never recorded as executed evidence |
| Skills, commands, subagents | `.claude/{skills,commands,agents}` in the project and `~/.claude` | valid frontmatter means *verified*; broken or missing files mean *configured, not usable* |
| MCP servers | `.mcp.json` and `~/.claude.json` (user and per-project scopes) | resolvable launch command means *installed*; missing command or disabled means *configured, not usable*; remote servers are never contacted |
| Plugins | `enabledPlugins` in user and project settings | *installed (unverified)* |

MCP entries contribute only their name, scope, transport, and the basename of the launch command. `env`, `headers`, `args`, and URLs are never read into the inventory, because they commonly contain credentials.

### Tool Selection Engine (`selection.py`)

For each required capability, candidates come from the registry (`data/tools.toml`) and from discovered skills, subagents, and MCP servers. The score is:

```text
availability (verified 10, installed 8, configured 3, missing 0)
+ kind preference (native/cli/script 3, skill/agent 2, mcp/service 1)
+ reliability - context_cost - 0.5*setup_cost - 0.5*security_exposure
```

Availability dominates. Among comparable options, the simpler integration wins, and the rationale says so. Language-specific tools are only considered for projects that use that language, and project scripts are only considered where they exist.

### Capability Research Engine (`research.py`)

`analyze_gaps` turns unmet requirements into gaps (blocking when *required*). `research()` queries `ResearchProvider`s. v0.1 ships `LocalRegistryProvider`. It returns a decision (`use-existing`, `fix-configuration`, `adopt`, `custom-or-research`), candidates with kind-specific security notes, validation steps, and explicit limitations. Records go to `.susanoo/records/research.jsonl`. New providers (package indexes, MCP registries) implement the same protocol.

### Verification Intelligence (`verification/`)

`[[verification]]` rules set a level per profile, escalate on dimension thresholds, can be limited to certain signals, and are forced to *required* by mandatory controls. Each step becomes:

- `ready`: a usable tool with a registry command; `susanoo verify --execute` runs it as an argv list (no shell) in the project root, with a timeout, and records the exit code, duration, and redacted output tail;
- `manual`: a usable tool that is operated by the agent or a person (for example the reviewer subagent);
- `unavailable`: a capability gap.

Identical commands run once per execution; later steps reuse the result and say so.

### Evaluation & Learning Engine (`evaluation/`)

- `RecordStore`: append-only JSONL files (`routes`, `outcomes`, `evidence`, `research`) under `.susanoo/records/`, which `susanoo init` git-ignores. Route records hold the assessment summary, profile, override, controls, gaps, and planned verification. Task text can be replaced by its SHA-256 (`records.store_task_text = false`).
- `summarize`: per-profile completion rate, duration, interventions, tokens, cost, regressions, and executed-check failure rate. Insights appear only after `min_samples` outcomes per profile (default 10) and are phrased as suggestions to review.
- `run_suite`: deterministic routing evaluation against a labeled suite (see [evaluation.md](../evaluation.md)).

### Claude Code adapter

- **`/susanoo` skill** (`.claude/skills/susanoo/SKILL.md`): `disable-model-invocation: true`, so it costs no context until you invoke it. It pre-approves only Susanoo's read and record commands. `susanoo verify --execute` runs project code, so it goes through normal permission prompts.
- **`susanoo-reviewer`**: independent review in a separate context, with no Edit or Write tools.
- **`susanoo-researcher`**: manual capability research with WebSearch and WebFetch. It never installs anything.
- **Installer**: manifest-based (`.susanoo/install.json`), atomic writes, conflict detection, backups on `--force`, and hash-checked uninstall.

Susanoo reuses Claude Code's own mechanisms (skills, subagents, plan mode, `--worktree` isolation, the permission system) instead of adding a separate orchestration layer.

### Enforcement (`enforcement/`, opt-in)

`gates.py` contains pure functions that take a hook event and the session's route state and return a decision: deny or ask on tool use, block on stop, or extra context. `state.py` derives the route state from records: active route for a session, approval, and missing or failing evidence. `hooks.py` translates between Claude Code's hook JSON and those decisions, and logs every intervention. The hooks are declared in the `/susanoo` skill's frontmatter, so they exist only in the Claude Code process that invoked the skill, not in a session resumed later. Compaction recovery (`session-start`) is wired through a settings hook instead, because Claude Code does not fire compaction events for skill hooks. See [enforcement.md](../enforcement.md).

## Data flow of `susanoo route`

1. Load configuration (defaults, then `.susanoo/config.toml`, then `--config`), profiles, and registry.
2. Build the inventory (read-only).
3. Assess the task with project context.
4. Route, then derive mandatory controls and checkpoints.
5. Select verification steps and collect capability requirements from the task, profile, and verification.
6. Recommend tools and analyze gaps.
7. Build the verification plan.
8. Render the brief or JSON. If the project is initialized, append a route record.

## Extension points

| To add... | Edit |
|---|---|
| a task signal | `[[signals]]` in `.susanoo/config.toml` (project) or `data/defaults.toml` (upstream) |
| a tool | `[[tools]]` in `.susanoo/tools.toml` or `data/tools.toml` |
| a capability | `data/capabilities.toml` |
| a profile tweak | `.susanoo/profiles/<name>.toml` |
| a safety control or verification rule | `[[controls]]` / `[[verification]]` in config |
| a research source | implement `research.ResearchProvider` |
| another AI coding environment | a new package under `adapters/` (discovery + installer + instructions) |
