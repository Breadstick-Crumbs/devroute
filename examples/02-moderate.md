# Example: Moderate feature

Command (run against `examples/sample-app`):

```bash
susanoo route --path examples/sample-app "Add a low-stock alert that emails the warehouse when a SKU drops below its reorder level"
```

Output:

````markdown
# Susanoo execution brief

## Task

Add a low-stock alert that emails the warehouse when a SKU drops below its reorder level

Detected signals: new functionality, notifications / messaging

## Assessment

Heuristic estimate from the task text and project files; not a validated measurement.

- Complexity:  MODERATE (0.32) - implementation_complexity=moderate
- Risk:        MODERATE (0.54) - external_dependencies=moderate
- Uncertainty: LOW (0.00)
- Confidence:  high
- Feature areas: notifications

| Dimension | Level | Why |
|---|---|---|
| implementation_complexity | 2 moderate | new functionality ('add') -> 2 |
| architectural_complexity | 0 none | - |
| affected_components | 1 low | notifications / messaging ('emails') -> 1 |
| technical_uncertainty | 0 none | - |
| external_dependencies | 2 moderate | notifications / messaging ('emails') -> 2 |
| security_privacy | 0 none | - |
| data_integrity | 0 none | - |
| irreversibility | 0 none | - |
| testing_difficulty | 0 none | - |
| parallelization | 0 none | - |

## Recommended mode: STANDARD

Typical feature or moderate modification. Investigate, plan, build incrementally, test, review, integrate.

Why: complexity is moderate; risk is moderate

- Planning: short written plan shared before editing
- Agents: optional: an independent reviewer subagent for the review phase
- Review: code review of the full diff; use the susanoo-reviewer subagent when risk is moderate or higher
- Isolation: feature branch recommended
- Verification depth: standard: unit tests, linting, and integration checks for touched boundaries

## Capabilities

Available tools:

- Integration testing [required]: pytest (cli, installed (unverified)) - `python -m pytest -q`
- Linting / static analysis [required]: Ruff (cli, installed (unverified)) - `ruff check .`
- Unit testing [required]: pytest (cli, installed (unverified)) - `python -m pytest -q`
- Version control [required]: git (cli, installed (unverified))
- Code navigation [recommended]: Claude Code Grep / Glob / Read (native, verified)
- Code review [recommended]: Claude Code subagents (native, verified)
- Type checking [recommended]: mypy (cli, installed (unverified)) - `mypy .`

Missing capabilities:

- Dependency audit [recommended, non-blocking]: consider pip-audit (cli), install: pip install pip-audit

Susanoo never installs tools itself; adopting any of these needs your approval.

## Architecture

Architecture context: None found. Follow the structure of the code you change; if the change adds a layer, a module boundary, or a new dependency direction, say so in the plan.

## Proposed steps

1. **Investigate** - Read the relevant code, tests, and conventions. Identify every component the change touches. Navigation: Claude Code Grep / Glob / Read.
2. **Clarify** - If requirements are ambiguous, ask focused questions. Skip when the task is already precise.
3. **Plan** **[checkpoint]** - Write a short plan: files to change, approach, tests to add, and risks.
4. **Implement incrementally** - Build in small steps, running relevant tests after each meaningful step.
5. **Test** - Add or update automated tests for new behavior and run the required verification.
6. **Review** - Review the complete diff for correctness, edge cases, and convention fit; delegate to susanoo-reviewer when risk is moderate or higher.
7. **Integration validation** - Confirm the change works with the rest of the system: run integration checks and the broader test suite.
8. **Report** - Report what changed, the verification that actually ran (with results), and remaining limitations.

## Required verification

- [required] Unit tests: `python -m pytest -q`
- [required] Linting / static analysis: `ruff check .`
- [required] Integration tests: `python -m pytest -q`
- [recommended] Type checking: `mypy .`
- [recommended] Dependency vulnerability audit: no usable tool - gap
- [recommended] Independent code review: Claude Code subagents (performed by the agent or a person)

Only checks that actually ran count as evidence. Run `susanoo verify --execute` to execute and record the ready checks.

## Checkpoints

Hands-off (the default): report these in your updates and continue; Susanoo waits for no approval. Mandatory safety controls still shape the work (review, sandbox credentials, rollback path), while confirming deploys, pushes, migrations, and other outward-facing actions is left to Claude Code's permission settings. Set `[enforcement] hands_off = false` to stop at each one.

- Approve the implementation plan before files are changed.

## Limitations

- The assessment uses configurable keyword heuristics and local project files; it can miss context that is not in the task description.
- Tool statuses come from read-only detection; run `susanoo inventory --verify` to check versions.
````
