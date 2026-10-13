# Example: Simple change

Command (run against `examples/sample-app`):

```bash
susanoo route --path examples/sample-app "Fix the typo in the OutOfStockError message"
```

Output:

````markdown
# Susanoo execution brief

## Task

Fix the typo in the OutOfStockError message

Detected signals: defect fix, small, contained change

## Assessment

Heuristic estimate from the task text and project files; not a validated measurement.

- Complexity:  LOW (0.00)
- Risk:        LOW (0.00)
- Uncertainty: LOW (0.33) - technical_uncertainty=low
- Confidence:  high

| Dimension | Level | Why |
|---|---|---|
| implementation_complexity | 0 none | defect fix ('fix') -> 1; capped at 0: small, contained change ('typo') |
| architectural_complexity | 0 none | - |
| affected_components | 0 none | - |
| technical_uncertainty | 1 low | defect fix ('fix') -> 1 |
| external_dependencies | 0 none | - |
| security_privacy | 0 none | - |
| data_integrity | 0 none | - |
| irreversibility | 0 none | - |
| testing_difficulty | 0 none | - |
| parallelization | 0 none | - |

## Recommended mode: FAST

Small, well-understood, low-risk change. Minimal planning, one agent, focused tests.

Why: complexity, risk, and uncertainty are all low

- Planning: minimal
- Agents: none: a single primary agent does the work
- Review: self-review of the final diff
- Isolation: none needed; work on the current branch
- Verification depth: focused: tests that cover the changed code

## Capabilities

Available tools:

- Unit testing [required]: pytest (cli, installed (unverified)) - `pytest -q`
- Linting / static analysis [recommended]: Ruff (cli, installed (unverified)) - `ruff check .`
- Version control [recommended]: git (cli, installed (unverified))

Missing capabilities: none for this task.

## Proposed steps

1. **Locate** - Find the exact code to change with targeted searches. Avoid broad repository exploration.
2. **Implement** - Make the smallest change that fully solves the task.
3. **Verify** - Run the tests that cover the changed code (add a regression test for bug fixes) and any required checks.
4. **Report** - Summarize the change, list the checks that actually ran with their results, and note anything not verified.

## Required verification

- [required] Unit tests: `pytest -q`
- [recommended] Linting / static analysis: `ruff check .`

Only checks that actually ran count as evidence. Run `susanoo verify --execute` to execute and record the ready checks.

## Approval checkpoints

- None required by Susanoo for this task. Normal judgment still applies.

## Limitations

- The assessment uses configurable keyword heuristics and local project files; it can miss context that is not in the task description.
- Tool statuses come from read-only detection; run `susanoo inventory --verify` to check versions.
````
