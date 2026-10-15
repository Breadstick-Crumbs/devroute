# 0014: `susanoo land`: land agent branches through one tested combined tree

**Status:** accepted (implemented, unreleased); amended by [0015](0015-lead-session-and-board.md), which lets an agent run `--merge` only through the hook's prompt and adds a review gate to it. A first, pull-request-based build of the landing queue in [0013](0013-leases-and-landing-queue.md) phase 3, and the records that 0013's phase 0 measurement needs. Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), and [0007](0007-hands-off-by-default.md), and amends 0007's list of prompts kept in every mode.

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
6. **Merge, only with `--merge`.** With several pull requests, every intermediate state of the base (the base plus the first *k*) is checked before the first merge, so a run that stops halfway never leaves the base on an untested tree. Then, for each pull request in order, the run checks that the base is exactly what it expects (the tested base for the first; for later ones, the merge commit of the previous pull request, whose tree must equal the tested prefix), updates and pushes the candidate, waits until the pull request shows the pushed head, checks the base again, and runs `gh pr merge <n> --merge --match-head-commit <pushed sha> --subject "Merge: <title> (#n)"` with the pull request's first paragraph as the body and no attribution lines. A moved base or head stops the run; a base that moves in the instant between the last check and the merge is detected right after it and stops the run. `--merge` also refuses a candidate whose landed tip is not the head its pull request shows, so the approval covers what GitHub shows, and (added by ADR 0015, `[land] require_review`, default true) a candidate without a passing `code-review` recorded for that tip. Without `--merge` the run ends at **ready to land** and prints the exact command and a one-line status to relay.
7. **Clean up** the scratch worktree and temporary refs, in every case.
8. **Record** the run in `<git common dir>/susanoo/records/land.jsonl`: candidates and tips, timings per stage, conflicts and how each was resolved, checks with their commands and results, flaky checks, the culprit and its kind, and what was pushed and merged. A passing result is reused by a later run only when the combined tree id and the exact check commands and environment match, so `land` followed by `land --merge` runs the checks once. Failures are never reused.

### Merging needs the user

- `--merge` merges only with the user's say-so. An agent may run it only through the PreToolUse prompt below, which asks the user in every mode, so the user still approves every merge, once. (Amended by ADR 0015, "Merging"; this rule first read "`--merge` is a flag the user gives. An agent never passes it on its own.")
- The PreToolUse hook asks the user before any `susanoo land ... --merge` command, in every mode, like `susanoo approve`. This adds it to ADR 0007's list of prompts kept in every mode. As in 0007, this is a guardrail against drift, not a security boundary.
- Each run names its pull requests, so one approval covers exactly the pull requests on that command line.

### Where this differs from 0013

Point by point, against the decisions 0013's review rounds settled:

1. **One entry per run, with resumable states (`land --next`): replaced.** One run lands the pull requests named on its command line. A run that is killed, for example by the Bash tool's ten-minute limit, leaves only its scratch worktree, and nothing that a later run has to resume. The rerun reuses every tree that already passed, through the pass cache. Orphaned scratch worktrees are removed by later runs, but only after 12 hours, because with no queue lock a younger one may belong to a concurrent run that is still working.
2. **`merge-tree --write-tree` plus `commit-tree`, checked in a detached worktree with hooks off and `[land] setup`: kept, except for the merge step.** The worktree is detached at the fetched base, repository hooks point at an empty folder, and `[land] setup` runs after every checkout. The candidates are merged with `git merge` inside that worktree instead of `merge-tree`, because keeping both sides of a conflict works on files and the checks need the checkout anyway. Each candidate builds on the previous one, which corresponds to 0013's "onto the previous landing".
3. **Configuration and registry from the applied base, never from the branch: kept.** Discovery also reads the base's tree. A candidate's change to `.susanoo/` is landed, but it takes effect only in later runs, once it is on the base. It is not flagged as coordinator-owned.
4. **Pass-only cache, never keyed by the tree alone: kept.** The key is the tree and the base tree, plus every check, compare, and setup argv, the `[land] env`, the resolved tool paths, and the Susanoo version. Lockfiles are part of the tree. Interpreter and tool versions are not in the key; the resolved paths stand in for them.
5. **Rerun once, then the branch alone on the base: kept and extended.** Before testing the branch alone, the prefixes are checked to find the first candidate that breaks the combination, and the base itself is checked when that candidate is the first one.
6. **`land --apply`: replaced by `--merge`, still always an ask.** The user's repositories land through GitHub pull requests, so the base is moved by `gh pr merge --match-head-commit` on the tested head, never by `merge --ff-only` on a local checkout. That means pushing: each candidate gets the base merged in (never rebased or forced) and is pushed before its merge. The conditions of `--apply` carry over: pull requests merge in order, and each merge requires the base to be exactly the tested state before it and to have exactly the tested tree after it. The hook asks before every `susanoo land ... --merge`, as 0013 asked for `--apply`.
7. **Re-chaining, and `land drop` to decline: not needed.** There is no chain to break. A base that moves invalidates nothing stored: the next run fetches the new base and tests a new tree. Declining a landing means not passing `--merge`.
8. **Shared state in the git common dir: kept for records. The lock: not built.** Nothing shared is rewritten. Records are append-only, one `os.write` per line. Each run's scratch worktree is a unique temp folder. Pushes are plain, never forced, and merges name the exact head, so a concurrent run that lands first makes the other fail its checks instead of overwriting anything. A lock comes back if queue state does.

Records go to the same shared location for a further reason: worktrees, and the `.susanoo/records` inside them, are deleted after their session, while every worktree of a repository writes to the same `land.jsonl`. Appends retry briefly when an antivirus scanner or another process holds the file.

### Go or no-go

These records decide whether landing becomes a product track of its own, by counts rather than validated thresholds, fixed here before the data exists. After at least 20 runs with two or more candidates:

- **Go** when at least one combined-tree failure is attributed to an interaction, or at least five runs resolved a conflict automatically, and the median time a run adds on top of its checks is under a minute.
- **No-go** otherwise: `land` stays a convenience command, and 0013's leases and notices are not built.

`susanoo land --stats` prints these counts. 0013's phase 0 rule still decides leases and notices.

## Consequences

- Every merge to `main` can go through one command that tests exactly the tree that lands and leaves evidence behind.
- `land` runs the project's checks, so it executes agent-written code on the host. It is an explicit command and says so.
- A run costs one full check run on the combined tree, plus reruns and prefix runs only when something fails, plus one per intermediate state when merging several pull requests.
- An agent's local branch falls behind its pushed branch after a run, and the run says so; Susanoo never moves a branch that is checked out in another worktree.
- Not in this version: forks (pull requests from another repository), batching with bisection, and GitLab or other forges.
