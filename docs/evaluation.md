# Evaluation methodology

DevRoute makes claims of two kinds, and each needs its own evidence:

1. **Routing behavior:** "this task gets this workflow." This is checked deterministically with a labeled suite.
2. **Workflow effectiveness:** "this workflow produces better results for tasks like this." This needs recorded outcomes from real or controlled runs.

v0.1 implements the infrastructure for both. **No effectiveness results are claimed yet.**

## 1. Routing evaluation (`devroute eval`)

The bundled suite (`src/devroute/data/suites/routing-v1.toml`) contains 31 tasks, each labeled with the profile the maintainers consider sensible and, for risky tasks, the minimum risk level the assessment must reach.

```bash
devroute eval                         # agreement, confusion matrix, mismatches
devroute eval --json
devroute eval --suite path/to/suite.toml --min-agreement 0.9 --fail-on-risk-violation
```

What the numbers mean:

- **Profile agreement** is the share of tasks routed to the labeled profile. It measures consistency with stated expectations, not correctness. The heuristics and the suite were developed together, so agreement on this suite is optimistic. Hold-out suites written independently are more informative.
- **Risk-floor violations** count tasks whose assessed risk fell below the labeled minimum. This is the safety-relevant number, and CI requires it to be zero.
- **Known mismatch (v0.1):** "Write unit tests for the invoice total calculator" routes DEEP because of the billing signal. A test-only change does not alter production behavior. This conservative error is left visible on purpose.

Use your own suite to calibrate DevRoute for a codebase. Write 20-50 representative tasks from your backlog, label them before looking at DevRoute's output, and tune `.devroute/config.toml` until agreement is acceptable with no risk-floor violations.

## 2. Workflow comparison protocol

To compare workflows (for example STANDARD vs DEEP on medium-risk tasks):

1. **Select paired tasks.** Choose tasks of comparable size and risk from the same codebase. Record why each pair is equivalent.
2. **Fix the environment.** Use the same model, Claude Code version, repository commit, and tool inventory (`devroute inventory --json` saved with the run).
3. **Assign profiles.** Use `devroute route --profile <name>` and alternate which task of each pair gets which profile. Mandatory controls still apply, which is intended.
4. **Execute.** Use `/devroute --profile <name> <task>` or the CLI brief. Do not intervene beyond the defined approval checkpoints. Count every extra intervention.
5. **Verify independently.** Run `devroute verify --execute`, then have a reviewer who does not know the profile assess correctness, defects, and maintainability.
6. **Record outcomes:**
   ```bash
   devroute outcome <record> --status completed --minutes 42 --interventions 1 \
     --tokens 180000 --cost 2.10 --regressions 0 --source human --notes "pair 3, arm B"
   ```
7. **Analyze.** `devroute report --json` gives per-profile aggregates. Report sample sizes, spread, and the full protocol with any result. With fewer than about ten pairs per comparison, describe observations rather than conclusions.

## 3. Learning loop

`devroute report` emits recommendations only after at least `--min-samples` (default 10) recorded outcomes per profile:

- low completion rate for a profile;
- frequent regressions;
- FAST tasks needing many human interventions;
- frequent overrides into a profile (a sign of miscalibrated thresholds).

Each recommendation names the configuration to review. **DevRoute never applies changes itself.** Edit `.devroute/config.toml`, then re-run `devroute eval` to see the effect.

## What is recorded

| Record | Fields |
|---|---|
| `routes.jsonl` | id, timestamp, version, task or task hash, profile, recommended, overridden, composite scores, dimension levels, signals, areas, controls, gaps, planned verification |
| `outcomes.jsonl` | record id, status, source (human/agent), duration, interventions, tokens, cost, regressions, agents, tools, notes |
| `evidence.jsonl` | record id, step, status, source (executed/reported), command, exit code, duration, redacted output tail |
| `research.jsonl` | capability, decision, candidates, validation steps, limitations |

All records stay on your machine in `.devroute/records/`, which is git-ignored by default.
