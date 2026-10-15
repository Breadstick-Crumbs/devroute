# Landing branches with `susanoo land`

When several agents work on one repository, each branch can pass its own checks and still break the others once combined: git catches textual conflicts, not semantic ones. `susanoo land` tests the exact tree that will land on the base branch, finds the branch that breaks it, and merges only when you say so. See [ADR 0014](decisions/0014-susanoo-land.md) for the reasoning.

It is a plain CLI: no model calls, nothing Claude-specific. It needs `git` 2.35 or later and, for pull requests, the GitHub CLI `gh`.

```bash
susanoo land 52 53            # test #52 and #53 together on origin/main, push, stop at "ready to land"
susanoo land 52 53 --merge    # the same, then merge both pull requests (you give this flag)
susanoo land feature-x        # a branch with or without a pull request
susanoo land 52 --no-push     # test only
susanoo land --stats          # what the recorded runs show
```

Candidates are pull request numbers (`52`; `#52` works too, but quote it in a shell, where `#` starts a comment) or branch names, landed in the order given. The base is the pull requests' base branch (or `--base`, default `main`) on `--remote` (default `origin`).

## What a run does

1. **Combine.** Fetches the base and every candidate, creates a scratch worktree at the base (in the system temp folder, with repository hooks off), and merges the candidates in order. A candidate's tip is its local branch or its remote branch, whichever contains the other; if they diverged, the run stops.
2. **Conflicts.** Each conflicting file is listed. Only append-only places are resolved automatically, by keeping both sides with the base branch's lines first: the `## [Unreleased]` section of `CHANGELOG.md` and the rows of an ADR index table (`docs/decisions/README.md`, `docs/adr/README.md`, sorted). A hunk qualifies only when both sides added lines, neither changed or removed any, and no added line is a heading. Anything else stops the run before a check runs.
3. **Checks on the combined tree.** The ready, required steps of the project's DEEP verification plan (what `susanoo verify --execute --required-only` runs), adjusted by `[[land.checks]]`, plus `[[land.compare]]` entries. Configuration is read from the base's tree, never from a candidate's.
4. **When something fails.** The failing checks are rerun once (a pass is recorded as flaky). Then they run on each prefix of the sequence (base plus the first *k* candidates) to find the first one that fails, and once on that candidate alone on the base:
   - **broken alone**: it fails on the base by itself;
   - **interaction**: it passes alone but fails with the candidates before it, a semantic conflict;
   - **base broken**: the base itself fails.
5. **Update and push.** The base is merged into the candidate (a merge commit; never a rebase or a force push), its tree is compared byte for byte with the tested tree, and it is pushed. Without `--merge`, only the first candidate is updated, because the later ones need the earlier merges on the base first. A branch with no remote copy is pushed to the remote (`--no-push` keeps it local). Your local branches are never moved; the run tells you when one is behind.
6. **Merge, only with `--merge`.** With two or more pull requests, every state the base passes through on the way (the base plus the first *k*) is checked first, so a stop halfway never leaves the base on an untested tree; a state that passed before is not rerun. Then, for each pull request in order: check that the base is exactly where it should be, update and push the branch, wait until the pull request shows the pushed head, check the base again, and run `gh pr merge <n> --merge --match-head-commit <pushed sha> --subject "Merge: <title> (#n)"`, with the pull request's first prose paragraph as the body and no attribution lines. After each merge, the base must have exactly the tested tree. A moved base stops the run before the merge; one that moves in the last instant is caught right after it, and the run stops. `--merge` refuses a branch whose local commits its pull request does not show yet: push the branch first. It also refuses, before any check runs, a candidate without a passing code-review recorded for the tip it tests (see "Review records").
7. **Clean up** the scratch worktree, in every case. A worktree left by a killed run is removed by a later run after 12 hours.
8. **Record** the run (below).

A run ends with a one-line `status:` you can relay, for example:

```text
status: land ready: #52 #53 on main@41a95d8; combined 9f8e7d6 6/6 checks; 1 conflict kept both; waiting for the user's --merge (land-20261015-101500-3fa2)
```

The checks run the candidates' code on your machine, as `susanoo verify --execute` does.

## Merging needs you

`--merge` merges only with your say-so. With Susanoo's hooks installed, Claude Code asks you before any `susanoo land ... --merge` command, in every permission mode. An agent may run it, but only through that prompt, so it cannot merge on its own ([ADR 0015](decisions/0015-lead-session-and-board.md), "Merging"); the approval covers exactly the pull requests on that command line. Without `--merge`, a run prints the command to run.

A passing result is reused when the combined tree, the base, and the check commands are the same, so `susanoo land 52 53` followed by `susanoo land 52 53 --merge` runs the checks once. Failures are never reused; `--no-cache` reruns everything.

## Configuration

In the project's `.susanoo/config.toml` (as committed on the base branch):

```toml
[land]
use_verification = true                 # run the DEEP plan's ready, required steps
require_review = true                   # --merge needs a passing code-review of each tested tip
setup = [["npm", "ci"]]                 # run in the scratch tree after every checkout
env = { PYTHONPATH = "{tree}/src" }     # "{tree}" is the scratch tree's path

[[land.checks]]                         # replace a verification step's command by id ...
id = "unit_tests"
command = ["pytest", "-n", "auto"]

[[land.checks]]                         # ... or add a check
id = "format_check"
command = ["ruff", "format", "--check", "."]

[[land.compare]]                        # a JSON-printing command, base vs combined tree
id = "routing_eval"
paths = ["src/app/data/"]               # only when the candidates change these (trailing "/" = folder)
command = ["python", "-m", "app", "eval", "--json"]
no_drop = ["profile_agreement"]         # must not fall below the base
no_rise = ["risk_floor_violations"]     # must not rise above the base

[[land.keep_both]]                      # replaces the default append-only places
path = "CHANGELOG.md"
section = '^## \[Unreleased\]'          # the enclosing "## " heading must match
```

`[[land.keep_both]]` also takes `lines` (every added line must match) and `sort = true`. Executables are looked up in the current checkout's and the main worktree's environments (`.venv`, `node_modules/.bin`) before `PATH`, so a fresh scratch tree runs with your tools. With no checks to run at all, `land` refuses rather than reporting a pass.

## Records and the go or no-go

Every run appends one line to `<git common dir>/susanoo/records/land.jsonl`, shared by all worktrees of the repository (agent worktrees and their `.susanoo/records` are deleted after their session). It holds the candidates and their tips, the time per stage and in checks, each conflict and how it was resolved, every check with its command and result, flaky checks, the culprit and its kind, and what was pushed and merged. It is local and never pushed.

`susanoo land --stats` counts the runs against the rule fixed in ADR 0014: after 20 runs with two or more candidates, landing becomes a product track if at least one failure was an interaction or at least five runs resolved a conflict automatically, and a run's median time on top of its checks is under a minute. These are counts, not validated thresholds.

## Review records

`susanoo review record <pr-or-branch> --tip <sha> --status passed|failed --tool code-review|security-review [--note ...]` records the result of a `/code-review` or `/security-review` against the commit it saw ([ADR 0015](decisions/0015-lead-session-and-board.md)). It refuses when `--tip` (a full commit id or at least 7 hex digits) is not the current tip: a pull request's head on GitHub (through `gh`), or for a branch its local or remote copy, whichever contains the other, as `land` picks it. When the remote cannot be reached, it refuses rather than trust the local copy. Records go to `<git common dir>/susanoo/records/reviews.jsonl`, next to `land.jsonl` and shared by all worktrees. `susanoo review list [<pr-or-branch>] [--json]` shows them.

`land` looks up the review of each candidate's tested tip (the tip before the base is merged in) and records the newest status per tool (`passed`, `failed`, or `missing`) in the run's record. With `[land] require_review = true`, the default, `--merge` refuses a candidate whose newest code-review of that tip is missing or failed, before any check runs, and checks again right before each merge, so a review that fails meanwhile still stops it. A review of an older commit does not count: a new commit needs a new review. The exception is a merge of the base that adds nothing: when `land` (or you) merged the base into a reviewed commit and nothing else changed, the review still counts, so `land` followed by `land --merge` works after the first run pushed such a merge. Without `--merge`, a missing review is a warning and the run's status line says so; the checks still run. A `security-review` is recorded in the run but not required by `land`, which cannot tell reliably which routes a branch carries; asking for one when the route has a security control is left to the lead session that ADR 0015 plans (not built yet). `require_review` is read only from the defaults and the base branch's `.susanoo/config.toml`, never from a branch or your user configuration, and when it is off and a review is missing, the run warns.

## Not supported yet

Pull requests from forks, forges other than GitHub, and batching with bisection. A candidate whose `.gitattributes` sets a `merge=` driver is refused, because it changes how git resolves conflicts.
