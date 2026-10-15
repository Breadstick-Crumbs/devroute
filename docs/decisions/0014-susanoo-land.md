# 0014: `susanoo land`: land agent branches through one tested combined tree

**Status:** accepted (implemented, unreleased). A first, pull-request-based build of the landing queue in [0013](0013-leases-and-landing-queue.md) phase 3, and the records that 0013's phase 0 measurement needs. Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), and [0007](0007-hands-off-by-default.md), and amends 0007's list of prompts kept in every mode.

## Context

Several agent sessions often work on one repository at once, each on its own branch and pull request. Running them in parallel is solved (the Claude desktop app, worktrees, Scion, Conductor). Landing their work is not. Git catches textual conflicts, not semantic ones: two branches can each pass their checks and still break each other once combined.

On 2026-10-15, pull requests #52 and #53 were landed by hand:

1. merge both onto the fetched `main` in a scratch worktree;
2. resolve the only conflict, two additions to `CHANGELOG.md`, by keeping both;
3. run the full suite on the combined tree;
4. merge `main` into each branch, check that its tree equals the tested one, push;
5. merge each pull request with `--match-head-commit` and the repository's subject style.

Every step is mechanical, and every step is easy to get subtly wrong by hand: testing a tree that is not the one that lands, merging a pull request whose head moved after the check, or keeping both sides of a conflict that was not append-only. ADR 0013 designed a local queue for this but planned to measure first. The user decided to land every merge through the command and measure from that real use instead, so the command comes first and the measurements are its records.

## Options

1. **Keep landing by hand.** No work, and no measurements.
2. **0013's local queue as designed** (`land enqueue`, `--next`, `--apply`, moving the local base with `--ff-only`, never pushing). The user's repositories land through GitHub pull requests, so a local base that moves on its own would then have to be pushed or reconciled with the pull requests anyway.
3. **One command that lands the pull requests named on the command line through a tested combined tree, and merges only with `--merge`.** Chosen.
4. **GitHub's merge queue.** It tests after push, needs branch protection the user declined, and knows nothing about agents.

## Decision

Option 3: `susanoo land <candidate>...`, where a candidate is a pull request number (`52`, `#52`) or a branch name. A deterministic command: no model calls, terse output, and nothing Claude-specific. It needs `git` 2.35 or later and, for pull requests, the GitHub CLI `gh`; any agent that can run a CLI can use it.

### What a run does

1. **Combine.** Fetch the base (default `main` on `origin`) and each candidate branch. A candidate's tip is its local branch or its remote branch, whichever contains the other; diverged copies stop the run. Create a scratch worktree, detached at the fetched base, with repository hooks disabled, and merge the candidates in the order given with `--no-ff`. Each prefix of that sequence is a commit, so "base plus the first *k* candidates" has a known tree.
2. **Conflicts.** A textual conflict is reported per file. Only places configured as append-only are resolved, by keeping both sides (`[[land.keep_both]]`, data): the `## [Unreleased]` section of `CHANGELOG.md`, and the rows of an ADR index table. A hunk qualifies only when both sides added lines and neither removed any (the `zdiff3` base part is empty), and no added line is a heading. The base side's lines come first, or the lines are sorted where the rule says so (table rows), so the same conflict resolves to the same bytes whichever side is "ours". Any other conflict stops the run with the file list.
3. **Check the combined tree.** The checks are the ready, required steps of the project's DEEP verification plan (what `susanoo verify --execute --required-only` would run), with `[[land.checks]]` replacing a step's command by id or adding checks (for this repository, the full suite with `pytest -n auto`). `[[land.compare]]` entries run a command that prints JSON on both the base and the combined tree, only when the candidates change the paths they name, and fail if a `no_drop` number fell or a `no_rise` number rose. This repository compares `susanoo eval` agreement and risk-floor violations whenever routing data changes. Configuration comes from the base's tree, never from a candidate, so a branch cannot change the checks that judge it. Commands run without a shell; `[land] env` and `[land] setup` prepare the scratch tree (for example `PYTHONPATH={tree}/src`), and executables are found in the invoking checkout's and the main worktree's environments first, so a fresh worktree tests its own code with the user's tools.
4. **Attribute a failure.** A failing check is rerun once; if it then passes it is recorded as flaky and the run goes on. Otherwise only the failing checks are run on each prefix in order. The first failing prefix names the culprit, and one more run decides the kind: the culprit alone on the base fails (**broken alone**), or passes (**interaction** with the earlier candidates, the semantic conflict this ADR exists for). When the first candidate is the culprit, the base itself is checked (**base broken**).
5. **Update and push.** Each candidate gets the base merged into it (a merge commit, never a rebase, never a force push), its tree is compared byte for byte with the tested prefix tree it must equal, and it is pushed with a plain fast-forward push. Without `--merge`, only the first candidate can be updated, because later ones need the earlier merges on the base first. `--no-push` skips this.
6. **Merge, only with `--merge`.** For each pull request in order, the run checks that the base is exactly what it expects (the tested base for the first; for later ones, the merge commit of the previous pull request, whose tree must equal the tested prefix), updates and pushes the candidate, and runs `gh pr merge <n> --merge --match-head-commit <pushed sha> --subject "Merge: <title> (#n)"` with the pull request's first paragraph as the body and no attribution lines. A moved base or head stops the run before anything is merged. Without `--merge` the run ends at **ready to land** and prints the exact command and a one-line status to relay.
7. **Clean up** the scratch worktree and temporary refs, in every case.
8. **Record** the run in `<git common dir>/susanoo/records/land.jsonl`: candidates and tips, timings per stage, conflicts and how each was resolved, checks with their commands and results, flaky checks, the culprit and its kind, and what was pushed and merged. A passing result is reused by a later run only when the combined tree id and the exact check commands and environment match, so `land` followed by `land --merge` runs the checks once. Failures are never reused.

### Merging needs the user

- `--merge` is a flag the user gives. An agent never passes it on its own.
- The PreToolUse hook asks the user before any `susanoo land ... --merge` command, in every mode, like `susanoo approve`. This adds it to ADR 0007's list of prompts kept in every mode. As in 0007, this is a guardrail against drift, not a security boundary.
- Each run names its pull requests, so one approval covers exactly the pull requests on that command line.

### Where this differs from 0013

- **Pull requests, not a local base.** Branches are pushed (base merged in, never forced) and merged through `gh`. 0013's `--apply` moved the local base with `--ff-only` and never pushed.
- **No queue state.** The order is the command line; nothing persists between runs except records and the pass cache. 0013's `queue.json`, chain, and one-step-per-run states are not built.
- **A scratch worktree for the combination**, not `git merge-tree --write-tree`, because the checks need a checkout anyway and the keep-both resolution works on files.
- **The intermediate trees are not checked by default.** Only the full combination is; the prefixes are checked when attributing a failure. Merging pull requests one by one passes through untested intermediate states of the base for the length of one run.
- **Shared records.** Records go to 0013's shared location, `<git common dir>/susanoo/`, because worktrees, and the `.susanoo/records` inside them, are deleted after their session; every worktree of a repository writes to the same file. Appends retry briefly when antivirus or another process holds the file.

### Go or no-go

These records decide whether landing becomes a product track of its own, by counts rather than validated thresholds, fixed here before the data exists. After at least 20 runs with two or more candidates:

- **Go** when at least one combined-tree failure is attributed to an interaction, or at least five runs resolved a conflict automatically, and the median time a run adds on top of its checks is under a minute.
- **No-go** otherwise: `land` stays a convenience command, and 0013's leases and notices are not built.

`susanoo land --stats` prints these counts. 0013's phase 0 rule still decides leases and notices.

## Consequences

- Every merge to `main` can go through one command that tests exactly the tree that lands and leaves evidence behind.
- `land` runs the project's checks, so it executes agent-written code on the host. It is an explicit command and says so.
- A run costs one full check run on the combined tree, plus reruns and prefix runs only when something fails.
- An agent's local branch falls behind its pushed branch after a run, and the run says so; Susanoo never moves a branch that is checked out in another worktree.
- Not in this version: forks (pull requests from another repository), batching with bisection, GitLab or other forges, and checking each prefix before merging.
