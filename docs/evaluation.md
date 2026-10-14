# Evaluation methodology

Susanoo makes claims of two kinds, and each needs its own evidence:

1. **Routing behavior:** "this task gets this workflow." This is checked deterministically with a labeled suite.
2. **Workflow effectiveness:** "this workflow produces better results for tasks like this." This needs recorded outcomes from real or controlled runs.

v0.1 implements the infrastructure for both. **No effectiveness results are claimed yet.**

## 1. Routing evaluation (`susanoo eval`)

The bundled suite (`src/susanoo/data/suites/routing-v1.toml`) contains 45 tasks, each labeled with the profile the maintainers consider sensible and, for risky tasks, the minimum risk level the assessment must reach.

```bash
susanoo eval                         # agreement, confusion matrix, mismatches
susanoo eval --json
susanoo eval --suite path/to/suite.toml --min-agreement 0.9 --fail-on-risk-violation
```

What the numbers mean:

- **Profile agreement** is the share of tasks routed to the labeled profile. It measures consistency with stated expectations, not correctness. The heuristics and the suite were developed together, so agreement on this suite is optimistic. Hold-out suites written independently are more informative.
- **Risk-floor violations** count tasks whose assessed risk fell below the labeled minimum, or whose route lacks a safety control the case lists in `controls`. This is the safety-relevant number, and CI requires it to be zero.
- **Known mismatch:** "Refactor the report builder without changing its output, and add a column to the users table" routes DEEP, although it is labeled STANDARD: "refactor" counts as an architectural change and stacks with "add". The negation itself works there (the database signal after the comma is kept). The v0.1 mismatch for test-only tasks ("Write unit tests for the invoice total calculator") was fixed in v0.5.
- **Labeling order:** cases are committed before the heuristics they test are changed, so labels are not fitted to output. When a heuristic is adjusted after seeing a result, the change is noted in the CHANGELOG.

A second suite, `src/susanoo/data/suites/ui-v1.toml`, holds 35 UI tasks for [ADR 0008](decisions/0008-ui-ux-workflow.md), each with the reason for its label. Run it from a source checkout with `susanoo eval --suite src/susanoo/data/suites/ui-v1.toml`. It was labeled before any UI heuristic changed and is kept apart from `routing-v1` so the original cases and CI's agreement threshold stay as they are. With v0.7 heuristics its first 15 cases agreed on 8. After the UI routing fixes it agrees on all 35 (20 more cases, mostly payment traps, were labeled before the fixes they test were run), and CI requires at least 90% agreement and 0 risk-floor violations, as for `routing-v1`.

```toml
[[cases]]
id = "ui-checkout-tax"
task = "Change how the checkout calculates tax"
expected = "deep"
min_risk = "critical"
controls = ["security-review", "data-safety", "external-sandbox"]   # must be in the route
```

Use your own suite to calibrate Susanoo for a codebase. Write 20-50 representative tasks from your backlog, label them before looking at Susanoo's output, and tune `.susanoo/config.toml` until agreement is acceptable with no risk-floor violations.

## 2. Workflow comparison protocol

To compare workflows (for example STANDARD vs DEEP on medium-risk tasks):

1. **Select paired tasks.** Choose tasks of comparable size and risk from the same codebase. Record why each pair is equivalent.
2. **Fix the environment.** Use the same model, Claude Code version, repository commit, and tool inventory (`susanoo inventory --json` saved with the run).
3. **Assign profiles.** Use `susanoo route --profile <name>` and alternate which task of each pair gets which profile. Mandatory controls still apply, which is intended.
4. **Execute.** Use `/susanoo --profile <name> <task>` or the CLI brief. Do not intervene beyond the defined approval checkpoints. Count every extra intervention.
5. **Verify independently.** Run `susanoo verify --execute`, then have a reviewer who does not know the profile assess correctness, defects, and maintainability.
6. **Record outcomes:**
   ```bash
   susanoo outcome <record> --status completed --minutes 42 --interventions 1 \
     --tokens 180000 --cost 2.10 --regressions 0 --source human --notes "pair 3, arm B"
   ```
7. **Analyze.** `susanoo report --json` gives per-profile aggregates. Report sample sizes, spread, and the full protocol with any result. With fewer than about ten pairs per comparison, describe observations rather than conclusions.

## 3. Learning loop

`susanoo report` emits recommendations only after at least `--min-samples` (default 10) recorded outcomes per profile:

- low completion rate for a profile;
- frequent regressions;
- FAST tasks needing many human interventions;
- frequent overrides into a profile (a sign of miscalibrated thresholds).

Each recommendation names the configuration to review. **Susanoo never applies changes itself.** Edit `.susanoo/config.toml`, then re-run `susanoo eval` to see the effect.

## What is recorded

| Record | Fields |
|---|---|
| `routes.jsonl` | id, timestamp, version, task or task hash, profile, recommended, overridden, composite scores, dimension levels, signals, areas, controls, gaps, planned verification |
| `outcomes.jsonl` | record id, status, source (human/agent), duration, interventions, tokens, cost, regressions, agents, tools, notes |
| `evidence.jsonl` | record id, step, status, source (executed/reported), command, exit code, duration, redacted output tail |
| `research.jsonl` | capability, decision, candidates, validation steps, limitations |

All records stay on your machine in `.susanoo/records/`, which is git-ignored by default.
