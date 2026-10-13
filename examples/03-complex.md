# Example: Complex, high-risk build

Command (run against `examples/sample-app`):

```bash
susanoo route --path examples/sample-app "Build a multi-warehouse inventory platform with user authentication, Stripe billing, and an admin dashboard"
```

Output:

````markdown
# Susanoo execution brief

## Task

Build a multi-warehouse inventory platform with user authentication, Stripe billing, and an admin dashboard

Detected signals: large-scope build, authentication / authorization, payments / billing, user interface

## Assessment

Heuristic estimate from the task text and project files; not a validated measurement.

- Complexity:  HIGH (1.00) - implementation_complexity=high, architectural_complexity=high, affected_components=high, testing_difficulty=high
- Risk:        CRITICAL (0.94) - security_privacy=high, data_integrity=high, external_dependencies=high
- Uncertainty: MODERATE (0.67) - technical_uncertainty=moderate
- Confidence:  high
- Feature areas: authentication, payments, user interface

| Dimension | Level | Why |
|---|---|---|
| implementation_complexity | 3 high | large-scope build ('build a multi-warehouse inventory platform') -> 3 |
| architectural_complexity | 3 high | large-scope build ('build a multi-warehouse inventory platform') -> 3 |
| affected_components | 3 high | authentication / authorization ('authentication') -> 1; user interface ('dashboard', 'admin dashboard') -> 1; 3 distinct feature areas: a... |
| technical_uncertainty | 2 moderate | large-scope build ('build a multi-warehouse inventory platform') -> 2 |
| external_dependencies | 3 high | payments / billing ('billing', 'stripe') -> 3 |
| security_privacy | 3 high | authentication / authorization ('authentication') -> 3; payments / billing ('billing', 'stripe') -> 3 |
| data_integrity | 3 high | payments / billing ('billing', 'stripe') -> 3 |
| irreversibility | 0 none | - |
| testing_difficulty | 3 high | large-scope build ('build a multi-warehouse inventory platform') -> 2; authentication / authorization ('authentication') -> 1; payments /... |
| parallelization | 2 moderate | 3 feature areas can be developed as separate units -> 2 |

## Recommended mode: DEEP

Complex, uncertain, or high-risk change. Architecture first, explicit plan, isolated work, independent review, evidence-backed completion.

Why: risk is critical; complexity is high; (also: uncertainty is moderate: investigate before changing code)

- Planning: explicit technical plan, reviewed before implementation (Claude Code plan mode is a good fit)
- Agents: recommended: independent reviewer subagent; optional parallel subagents for independent feature areas
- Review: independent review by a separate agent context (susanoo-reviewer), plus human review of security-sensitive parts
- Isolation: isolated git worktree or feature branch (for example `claude --worktree <name>`)
- Verification depth: comprehensive: unit, integration, security, and end-to-end checks matched to the risks found

## Mandatory safety controls

These apply regardless of the selected profile.

- **security-review** (security_privacy is high): Independent security-focused review of the diff: authentication, authorization, input handling, secrets, and data exposure.
- **data-safety** (data_integrity is high): Verify schema and data changes against a non-production database, keep a rollback path, and avoid destructive operations on real data.
- **external-sandbox** (external_dependencies is high): Use sandbox or test-mode credentials for third-party services; never use live keys during development or testing.

## Capabilities

Available tools:

- Code review [required]: Claude Code subagents (native, verified)
- Integration testing [required]: pytest (cli, installed (unverified)) - `pytest -q`
- Security analysis [required]: Bandit (cli, installed (unverified)) - `bandit -r . -q -x ./.venv,./venv,./node_modules,./tests,./build,./dist`
- Linting / static analysis [required]: Ruff (cli, installed (unverified)) - `ruff check .`
- Type checking [required]: mypy (cli, installed (unverified)) - `mypy .`
- Unit testing [required]: pytest (cli, installed (unverified)) - `pytest -q`
- Version control [required]: git (cli, installed (unverified))

Missing capabilities:

- Migration checks [required, blocking until resolved or explicitly accepted]: consider Alembic (cli)
- Payment sandbox [required, blocking until resolved or explicitly accepted]: consider Stripe CLI (cli)
- Secret scanning [required, blocking until resolved or explicitly accepted]: consider Gitleaks (cli)
- Browser / end-to-end testing [recommended, non-blocking]: consider Playwright (cli)
- Dependency audit [recommended, non-blocking]: consider pip-audit (cli)

Susanoo never installs tools itself; adopting any of these needs your approval.

## Proposed steps

1. **Requirements and architecture analysis** **[approval]** - Establish requirements, constraints, and the architecture. Surface open questions instead of guessing.
2. **Dependency and risk assessment** - List external dependencies, security and data risks, and irreversible steps. Confirm the safety controls below.
3. **Technical plan** **[approval]** - Write an explicit plan with milestones, interfaces, test strategy, and rollback approach.
4. **Targeted research** - Only when justified by missing capabilities or high uncertainty: research options and record findings (`susanoo research`).
5. **Isolated implementation** - Implement milestone by milestone in an isolated branch or worktree. Delegate independent feature areas to subagents only when they do not share files.
6. **Independent review** - Have a separate agent context (susanoo-reviewer) review the full diff against the plan and the risks.
7. **Integration and security verification** - Run the full required verification set and record results as evidence (`susanoo verify --execute`).
8. **Evidence-backed completion** **[approval]** - Report results with the recorded evidence, unresolved risks, and anything that was not verified.

## Required verification

- [required] Unit tests: `pytest -q`
- [required] Linting / static analysis: `ruff check .`
- [required] Type checking: `mypy .`
- [required] Integration tests: `pytest -q`
- [required] Security analysis: `bandit -r . -q -x ./.venv,./venv,./node_modules,./tests,./build,./dist`
- [required] Secret scanning: no usable tool - gap
- [required] Database migration check: no usable tool - gap
- [required] Independent code review: Claude Code subagents (performed by the agent or a person)
- [recommended] End-to-end / browser tests: no usable tool - gap
- [recommended] Dependency vulnerability audit: no usable tool - gap

Only checks that actually ran count as evidence. Run `susanoo verify --execute` to execute and record the ready checks.

## Approval checkpoints

- Approve requirements and architecture before planning details.
- Approve the technical plan before implementation starts.
- Approve the final change after independent review and verification.
- Human approval before merging security-sensitive changes.
- Human approval before running migrations or data-modifying operations outside a disposable environment.
- Human confirmation that only test-mode credentials are configured.

## Limitations

- The assessment uses configurable keyword heuristics and local project files; it can miss context that is not in the task description.
- Tool statuses come from read-only detection; run `susanoo inventory --verify` to check versions.
````
