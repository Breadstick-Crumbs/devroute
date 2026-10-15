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
- Integration testing [required]: pytest (cli, installed (unverified)) - `python -m pytest -q`
- Security analysis [required]: Bandit (cli, installed (unverified)) - `bandit -r . -q -x ./.venv,./venv,./node_modules,./tests,./build,./dist`
- Linting / static analysis [required]: Ruff (cli, installed (unverified)) - `ruff check .`
- Type checking [required]: mypy (cli, installed (unverified)) - `mypy .`
- Unit testing [required]: pytest (cli, installed (unverified)) - `python -m pytest -q`
- Version control [required]: git (cli, installed (unverified))
- Code navigation [recommended]: Claude Code Grep / Glob / Read (native, verified)

Missing capabilities:

- Accessibility scanning [required, blocking until resolved or explicitly accepted]: consider axe CLI (cli), install: npm install -g @axe-core/cli
- Migration checks [required, blocking until resolved or explicitly accepted]: consider Alembic (cli), install: pip install alembic
- Payment sandbox [required, blocking until resolved or explicitly accepted]: consider Stripe CLI (cli), install: https://docs.stripe.com/stripe-cli
- Secret scanning [required, blocking until resolved or explicitly accepted]: consider Gitleaks (cli), install: https://github.com/gitleaks/gitleaks#installing
- Visual check [required, blocking until resolved or explicitly accepted]: consider Playwright (cli), install: npm init playwright@latest   (or: pip install pytest-playwright && playwright install)
- Browser / end-to-end testing [recommended, non-blocking]: consider Playwright (cli), install: npm init playwright@latest   (or: pip install pytest-playwright && playwright install)
- Dependency audit [recommended, non-blocking]: consider pip-audit (cli), install: pip install pip-audit

Susanoo never installs tools itself; adopting any of these needs your approval.

## UI

UI domain: Dashboard or data-heavy app (the task mentions "dashboard"; set [ui] domain to change). This is a heuristic, not a verified classification.

Design context: None found. Suggest the user add a DESIGN.md (product and users, references, tokens, components, do's and don'ts) so UI work starts from it; Susanoo never creates it.

UI baseline (every UI change):

- Text contrast at least 4.5:1 (3:1 for large text and for UI components and focus indicators); WCAG 2.2 AA 1.4.3 and 1.4.11.
- Pointer targets at least 24x24 CSS px, or enough space around them (WCAG 2.2 AA 2.5.8); 44x44 (2.5.5, level AAA) is the better aim.
- Everything works with the keyboard alone, in a sensible order, with a visible focus indicator that sticky headers or banners never fully hide (2.1.1, 2.4.3, 2.4.7, 2.4.11).
- Empty, loading, and error states, plus long content and very long words.
- Check at 375, 768, and 1280 CSS px wide (common widths, not a standard).
- Use the project's design tokens and components rather than one-off values.

Sources: WCAG 2.2 (W3C), level AA unless noted; the states, long-content, and design-token items are common practice, and the widths are conventions.

Dashboard or data-heavy app:

- Summary first, then drill down; a few headline metrics (practitioners suggest about 3-5).
- Tables: sticky header, key column pinned, visible sort state, keyboard row selection.
- Comfortable and compact density where people scan many rows.
- Consistent number formats and units; numbers right-aligned.
- Charts label axes and units and never rely on color alone (WCAG 1.4.1).
- Every widget has loading, empty, and error states; large data sets are paginated or virtualized.
- Filter and sort state stays visible.

Sources: Practitioner guidance (for example Stephanie Walter on complex data tables, UX Studio on dashboards), not peer-reviewed research; WCAG for color.

## Proposed steps

1. **Requirements and architecture analysis** **[checkpoint]** - Establish requirements, constraints, and the architecture. Surface open questions instead of guessing. Navigation: Claude Code Grep / Glob / Read.
2. **Dependency and risk assessment** - List external dependencies, security and data risks, and irreversible steps. Confirm the safety controls below.
3. **Technical plan** **[checkpoint]** - Write an explicit plan with milestones, interfaces, test strategy, and rollback approach.
4. **Targeted research** - Only when justified by missing capabilities or high uncertainty: research options and record findings (`susanoo research`).
5. **Isolated implementation** - Implement milestone by milestone in an isolated branch or worktree. Delegate independent feature areas to subagents only when they do not share files.
6. **Independent review** - Have a separate agent context (susanoo-reviewer) review the full diff against the plan and the risks.
7. **Integration and security verification** - Run the full required verification set and record results as evidence (`susanoo verify --execute`).
8. **Evidence-backed completion** **[checkpoint]** - Report results with the recorded evidence, unresolved risks, and anything that was not verified.

## Required verification

- [required] Unit tests: `python -m pytest -q`
- [required] Linting / static analysis: `ruff check .`
- [required] Type checking: `mypy .`
- [required] Integration tests: `python -m pytest -q`
- [required] Visual check: no usable tool - gap
  Render the change and look at it at 375, 768, and 1280 CSS px (common widths, not a standard), compared with the request or a reference. Record it: `susanoo evidence latest --step visual_check --status passed --note "<widths checked, screenshot paths>"`, or `--status skipped --note "<why>"` when nothing visible changed.
- [required] Accessibility scan: no usable tool - gap
  A scan finds only part of the problems; also check keyboard operation and a visible focus indicator.
- [required] Security analysis: `bandit -r . -q -x ./.venv,./venv,./node_modules,./tests,./build,./dist`
- [required] Secret scanning: no usable tool - gap
- [required] Database migration check: no usable tool - gap
- [required] Independent code review: Claude Code subagents (performed by the agent or a person)
- [recommended] End-to-end / browser tests: no usable tool - gap
- [recommended] Dependency vulnerability audit: no usable tool - gap

Only checks that actually ran count as evidence. Run `susanoo verify --execute` to execute and record the ready checks.

## Checkpoints

Hands-off (the default): report these in your updates and continue; Susanoo waits for no approval. Mandatory safety controls still shape the work (review, sandbox credentials, rollback path), while confirming deploys, pushes, migrations, and other outward-facing actions is left to Claude Code's permission settings. Set `[enforcement] hands_off = false` to stop at each one.

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
