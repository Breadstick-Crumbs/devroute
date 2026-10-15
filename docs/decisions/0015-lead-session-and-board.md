# 0015: A lead session per repository, and a read-only `susanoo board`

**Status:** accepted, built in slices. Slice 1, evidence bound to a commit and review records, is implemented (unreleased); the rest is still to build and stays marked *proposed* below (see "Slices"). [0012](0012-delegation-plans.md) and [0013](0013-leases-and-landing-queue.md) are proposed; [0014](0014-susanoo-land.md) is accepted. Builds on 0014's `susanoo land` and amends two of its rules (see "Merging" and "Reviews"), and replaces [0017](0017-route-records-across-worktrees.md)'s `commit` evidence field with `head`. Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), [0007](0007-hands-off-by-default.md), [0009](0009-clarifying-questions-under-hands-off.md), and [0016](0016-app-messages-are-not-the-users-message.md).

## Context

The user runs several Claude Code sessions on one repository at once, each in its own worktree and pull request. Running them is solved. Working with them is not. On this repository in October 2026:

- **The user is the message bus.** One session was put "in charge of merges", and the others had to wait for its go-ahead. Brief wording was "agreed with" another session by the user carrying text between chats. Asking one chat to check another chat's work was a manual step every time.
- **Nobody sees the whole picture.** Seven worktrees existed besides the main checkout; three of them were already merged and still checked out. The main checkout itself was on a feature branch. Which branch touched which files, and which had passing evidence for its current commit, was only in the user's head.
- **Shared files collide.** `CHANGELOG.md`, `docs/roadmap.md`, the ADR index, and ADR numbers changed in several branches at once. The delegation ADR was drafted as 0011 and had to be renumbered because another session took 0011 first.
- **Merging was hand work.** 0014's `susanoo land` replaces it, but `land` starts when the branches are done.

0012 plans the top-down split of one large task into units. The user's work mostly looks different: many small, independent requests ("fix this", "fix that") arriving while earlier ones are still running. 0013 named a "board" for this case but did not design it.

**The constraint on this design.** Outcomes depend mostly on the model. In a pilot run in another session, plain Claude (Opus 5.5) without Susanoo avoided all 14 traps rebuilt from the user's real failures, and on one task Susanoo cost $0.19 against $0.08 without it, with both passing. These are single small runs, not validated results, but they point the same way as the user's principle: Susanoo's value is in checks and gates, not in orchestration or guidance the model and Claude Code already provide. So this ADR uses those features as they are and builds only what they lack.

## What the model and Claude Code already provide here

The desktop app features below are described as observed in October 2026. They are not a documented interface and are rechecked before anything depends on them.

| Need | Already provided | Gap Susanoo fills |
|---|---|---|
| Start a worker in its own worktree with a brief | the app's session start (fresh or forked context, own worktree) | none |
| Hear when a worker is done, without polling | notes from linked sessions when they finish a turn; the "Linked sessions now" note | none; the worker's report format is skill text |
| Send follow-ups, review findings, rebase requests | messages to a linked session | none |
| See which sessions exist and their state | listing and reading sessions; sidebar groups | none |
| Archive finished workers | archiving sessions | none |
| Fix CI failures and answer review comments on a pull request | the app's pull-request Auto-fix | none |
| List worktrees and branches | `git worktree list`, read by the model | none |
| Review a diff | the built-in `/code-review`, and `/security-review` | **recording a review against the exact commit it saw, and refusing to merge without one** |
| Know whether a branch's checks passed **for its current commit** | nothing: evidence records have no commit today | **a `head` field on evidence, and the board** |
| Know which branches change the same files | nothing in one view; the model can run `git diff` per branch, at a token cost on every request | **overlaps on the board, computed deterministically** |
| Know whether two branches took the same ADR number | nothing | **the board flags it** |
| Land several branches through one tested tree | nothing | `susanoo land` (0014) |
| Keep the lead's own messages from being routed as work | nothing: automatic routing routes every message | **`susanoo lead on`** |
| Remember queued requests and expected files across a long lead conversation | the model can keep notes in a file | none; the ledger is a file Claude writes |

## Options

1. **Keep going as now.** The user starts sessions, relays between them, and decides when to merge.
2. **Susanoo as orchestrator.** Rejected by ADR 0003, and by the table above: the app already starts, watches, and messages sessions.
3. **Scion or another container runner.** Containers isolate agents, which worktrees already do; they do not coordinate them. Parked, as is 0012's plan format, until something shows a need the native features do not meet.
4. **Claude Code's agent teams.** Experimental and CLI only; whether teammates land through pull requests is not established (open question 3).
5. **A lead session defined by a skill on top of the app's own session features, plus the few checks the table names as gaps.** Chosen.

## Decision

Option 5. The user talks to one session, the **lead**. The lead never edits tracked project files; it hands every change to a **worker** session through the app, has each finished worker's pull request reviewed with `/code-review`, lands it with `susanoo land`, and the user approves each merge batch once. Susanoo runs no agents and adds no orchestration code: the lead is a skill, and Susanoo's new code is the checks in the table's right-hand column.

### Roles

- **Lead.** One session per repository, in the main checkout, which stays on the base branch, clean and fast-forwarded to the remote base, because the lead reads the code there and the app starts workers from it. It answers questions itself and turns every request that would change files into a worker. It is the only session that messages workers, assigns ADR numbers, and writes the project's state notes (such as Claude's project memory).
- **Worker.** One session per task, in its own worktree and branch, started by the lead from the freshly updated base. It routes and verifies as any session does today, provided Susanoo's hooks are active in its worktree (global hooks, or project hooks committed to the repository; hooks in a git-ignored `settings.local.json` are not copied into new worktrees). It ends by pushing its branch and opening a pull request, and leaves CI failures and review comments to the app's Auto-fix. It asks the lead, not the user, when it is blocked; the lead asks the user only when it cannot answer itself (ADR 0009).
- **User.** Says what they want to the lead, answers clarifying questions, and approves merges. They can still talk to a worker directly; that work still lands through the lead.

### The lead skill

*Proposed:* a `susanoo-lead` skill, installed by `susanoo init` next to `/susanoo`. It is instructions, not code (ADR 0003). The loop:

1. **Intake.** Read the app's linked sessions, `susanoo board`, and the ledger. If a running worker already owns the area, send it the request as a follow-up, unless its pull request is in a `land` run. If the request will likely touch files a running worker has changed, queue it in the ledger until that worker lands, or give it to that worker. Otherwise start a new worker.
2. **Dispatch.** Fast-forward the main checkout to the remote base. Start the worker with a brief whose **first line is the user's request in their words**, then what the lead found when reading, the files it expects the worker to touch, and an ADR number if one is needed. The worker rules are not pasted in; the brief names the skill section that holds them, so they are not routed as part of every task. Put the worker in the repository's sidebar group, and note it in the ledger.
3. **Wait.** The app's note arrives when the worker finishes a turn. A note is a claim, not proof.
4. **Check.** The worker's board row must show its checks passing for its current commit. Then run `/code-review` on its pull request, and `/security-review` too when the worker's route carries a security control (the board marks it). Record the result with `susanoo review` (below). Findings go to the worker as a message, and step 3 repeats.
5. **Land.** Run `susanoo land <pr>...` without `--merge`. A failed or conflicting run goes back to the culprit worker, with `land`'s attribution.
6. **Merge.** See "Merging".
7. **Tidy.** Fast-forward the main checkout, take queued requests from the ledger, archive finished worker sessions through the app, and remove worktrees the app did not create only with the user's say-so.

**The ledger** is `<git common dir>/susanoo/lead.md`: a short Markdown file the lead writes, outside the working tree, never committed, shared by every worktree. It holds what neither git nor the app keeps: queued requests, expected files per worker, ADR numbers handed out, and review rounds. Susanoo neither writes nor parses it. With it, a fresh lead can take over from one whose context has grown long.

### Worker rules

Held in the skill, so they are data, not code.

- When the lead sends a new request, not findings on the current one, route it with `susanoo route` before editing.
- Change only what the task needs. If it needs files another worker is likely changing, tell the lead before editing them.
- Use the ADR number the lead gave, or ask for one.
- Append to `CHANGELOG.md` under *Unreleased* only, without adding headings or editing other entries; that is what 0014 can resolve. Edit only your own rows of `docs/roadmap.md`, as AGENTS.md already asks.
- Do not merge, rebase onto, or edit another worker's branch. After a `land` run has pushed your branch, run `git merge --ff-only origin/<your branch>` before more edits.
- Finish in this order: commit, `susanoo verify --execute`, push, open a pull request. A later commit needs verification again.
- End with a report whose **first line** is the status, because the lead may see only the start of it: `READY pr=#57`, `BLOCKED <reason>`, or `NEEDS-INPUT <question>`.

### Routing the lead

Under automatic routing, every message to the lead would get a brief telling it to investigate, plan, and verify, and would leave a route in the main checkout's records that never gets an outcome, beside the worker's route for the same task. [ADR 0016](0016-app-messages-are-not-the-users-message.md) already keeps the app's own prompts out of routing: worker notes, peer messages, and CI events reach the lead without opening a route. The user's own messages are still routed, and nothing native prevents that. *Proposed:* `susanoo lead on`, run by the lead skill, marks the current session as a lead in the hook state, keyed by session id. Its prompts are not routed; the hook adds one short line instead. `susanoo lead off` undoes it.

The same ADR decides how workers see the lead's messages. A worker's brief is its session's first message, so it is routed as the user's message. Everything the lead sends later (review findings, a rebase request, a follow-up request) arrives as a message from another session: it opens no route and keeps the worker's open route and floor. Review findings and rebase requests belong to the open route. A **new request** sent to a running worker does not, so the worker routes it itself with `susanoo route`, never below its open route; the autonomy and named workflow of the user's first message do not carry over to it (0016).

### Evidence bound to a commit

*Implemented (slice 1):* every evidence record Susanoo writes, executed (`susanoo verify --execute`) or reported (`susanoo evidence`), gets `head` (the commit, `git rev-parse HEAD` of the checkout the checks ran in) and `dirty` (whether tracked files, or untracked files that are not ignored, differed from it), recorded by Susanoo itself, never taken from the agent: neither command has an option for them. This is a guardrail against drift, not a security boundary (as in 0007): the agent runs these commands in its own environment and could edit the local records. This is the field 0012 proposed, adopted here on its own.

- **When it is read.** For executed evidence, before and after the checks. The evidence names the commit checked out when they started, and is `dirty` when the tree was dirty before or after, or HEAD moved while they ran, so a check that leaves files behind, or work committed mid-run, never yields clean evidence. Reported evidence is read when the report is made; Susanoo cannot know when a reported check ran. So a review done on one commit and reported after a later commit is bound to the later one; slice 2 closes this for the review step by requiring a review record for the tip (below) rather than a report.
- **Unknown.** A folder outside a git repository, a repository without a commit, a missing `git`, or any git failure records `head: null` and `dirty: null`; recording never fails because of it. Git is run with an argv list, without optional locks or fsmonitor, and with variables that point it at another repository (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, ...) or add configuration (`GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT` and its keys) removed; `susanoo land` and `susanoo review` run git the same way. `git status` does not look at files marked assume-unchanged or skip-worktree (sparse checkouts use the latter), so when any file is marked, `dirty` is unknown rather than false. Files ignored through `.git/info/exclude` or `core.excludesFile` count as ignored, as git treats them. A project inside a larger repository records that repository's commit.
- **Old records** stay readable: a missing field means unknown. The evidence gate and the Stop hook are unchanged; they do not read the new fields.
- **ADR 0017's `commit`.** 0017 added `checkout` and `commit` to evidence recorded for a route in another checkout. `head` is the same value, recorded for every evidence record and read by git rather than from `.git` files, so `commit` is no longer written (it was never released) and `checkout` is kept: the gate counts runs per checkout by it. Evidence that has `commit` but no `head` does not count as verified.

A branch is **verified** when every required step of its newest route has newest evidence that the gate accepts (a passing executed run for a step Susanoo can run; a reported pass or explained skip for one it cannot) and has `head` equal to the branch tip and `dirty` false. The draft said "executed" for every step; that would make no DEEP route ever verified, because its independent review is a step only a report covers. Evidence without `head` never counts as verified. *Implemented* as `is_verified(route, evidence, tip)` (and `unverified_steps`, which names the steps that do not hold) in `enforcement/state.py`, for the board and `land` to use; no command reads it yet. In detail:

- The steps are those the evidence gate demands: required steps, except a step whose tool is missing on an automatic route.
- A step holds when the gate accepts it **and** the newest evidence the gate counts for it has `head` equal to the tip (a full commit id) and `dirty` exactly false. For a step Susanoo can run, that is a passing executed run; a skipped "run" is not a run. For a step it cannot run (an independent review, a manual check), it is what the gate accepts there, a reported pass or an explained skip, also bound to the tip: requiring an executed run for those would make no DEEP route ever verified.
- Requiring the gate too keeps 0017's rule: a failure in one checkout is not cleared by a pass in another.
- A route with no required step is never verified, because nothing bound it to a commit.

### Reviews

The review itself is the built-in `/code-review` (and `/security-review`). Susanoo records it and gates on it.

- *Implemented (slice 1):* `susanoo review record <pr-or-branch> --tip <sha> --status passed|failed --tool code-review|security-review [--note ...]`. It refuses when `--tip` is not the branch's current tip, so a review of an older commit cannot be recorded as a review of the newer one. The tip of a pull request (`52`, `#52`) is its head on GitHub, from `gh pr view`; `gh` is needed only then. The tip of a branch is its local or its remote copy (after fetching that one branch), whichever contains the other, as `land` picks a candidate's tip; diverged copies are refused. A branch counts as local only when the repository has no such remote or the remote answers that it lacks the branch; a remote that cannot be reached is refused, because a newer commit may be there. `--tip` is a full commit id or a prefix of at least 7 hex digits; the record always holds the full tip. Records go to `<git common dir>/susanoo/records/reviews.jsonl`, next to `land`'s records, because the lead and the workers live in different worktrees. Each holds the target as given, branch, pull request number, tip, tool, status, a one-line redacted note, the session id, and the checkout it was recorded from.
- *Implemented (slice 1):* `susanoo review list [<pr-or-branch>] [--last N] [--json]` shows them, matched by number or branch name without looking anything up, so it works offline. `review_for(reviews, tip, tool)` in `land/reviews.py` returns the newest review of exactly that commit, for the gate below.
- *Implemented (slice 2):* **amends 0014:** `land --merge` refuses a candidate without a passing `code-review` recorded for the tip it tested before merging in the base. The newest code-review of exactly that commit decides, so a later failed review overrides an earlier pass, and a review of an older commit does not carry over to a new one. A review also covers merges of the base on top of the reviewed commit that add nothing of their own (the second parent is on the base, and merging it into the first parent as `land` does gives exactly the merge's tree), up to three of them, so `land` followed by `land --merge` works after `land` pushed the base merged into a branch; these commits carry the same reviewed changes, so the newest review of any of them decides (a later failed review still overrides an earlier pass), and a merge that adds changes, or one `land` would not make, needs its own review. It refuses before any check runs, and reads the records again right before each pull request's merge, so a review that fails while the checks run still stops it. Without `--merge`, `land` reports the missing or failed review in its output, its warnings, and its one-line status, and goes on, so the combined checks still run. Each run records in `land.jsonl`, per candidate, the newest review status of its tip for each tool (`passed`, `failed`, or `missing`), and whether a review was required. The review is required when `[land] require_review = true`, the default; it is read only from the defaults and the base's own project file, never from a candidate or the user configuration (`~/.susanoo/config.toml`, which an agent can change without a merge), and when it is off and a review is missing, the run warns.
  - **No `security-review` in the land gate.** Which routes a candidate carries is not known reliably from `land`'s side: a branch has no route of its own, its routes live in its worktree's records or in another checkout's (0017), that worktree may be gone, and one branch can carry several routes. So `land` records a `security-review` of the tip when there is one but never requires it. Asking for it is left to the lead skill (step 4, from the board's `security` mark; slice 4), and "verified" requires it where the route is known (next point).
  - **"Verified" takes the review step from review records.** `is_verified` and `unverified_steps` take an optional `reviews` argument, the review records. With it, the step a review stands for, `independent_review`, holds only when the evidence gate accepts the step and the newest `code-review` of the tip passed, plus the newest `security-review` when the route carries the `security-review` control. A reported result is bound to the commit it was reported on, not the commit that was reviewed, so it no longer stands in for the review there; an explained skip alone, a failed review, or a review of another commit does not count (a reported skip with a passing review of the tip does, since the gate accepts the skip). A step the gate refuses never holds, so this is never looser than the gate. Without `reviews` the helpers behave as in slice 1. Which steps a review stands for is a table in `enforcement/state.py`, tested against the step and control ids in `defaults.toml`.

### Merging

0014 asks the user, through the PreToolUse hook and in every mode, before any `susanoo land ... --merge`. That prompt is the merge question; the lead does not also ask in chat. The lead posts one line naming the ready pull requests and what each does, then runs `susanoo land <pr>... --merge`. **Amends 0014's** "`--merge` is a flag the user gives. An agent never passes it on its own." to: an agent may run it only through that prompt, so the user still approves every merge, once.

### `susanoo board`

*Proposed:* `susanoo board [--base <branch>] [--json]`. It shows only what the app and plain git listing do not: per branch, whether its checks and review hold **for its current commit**, its `land` status, and conflicts with other branches. It does not list sessions, activity, pull requests, or cleanup candidates; the app does.

One line per worktree branch, for example:

```text
lead-board   verified@a1b2c3d  review:passed  land:ready     security
fix-verify   stale (2 commits) review:-       land:-         overlap: src/susanoo/cli.py (taste-fix)
taste-fix    unverified        review:-       land:failed    ADR 0016 also taken by docs-x
```

Eight branches fit in well under 1 KB.

**What it reads.** Local only; no network and no transcripts:

- `git worktree list --porcelain`, to find branches; detached, locked, prunable, and `land`'s temporary worktrees are left out or marked.
- Per branch, with `git --no-optional-locks -c core.fsmonitor=false`:
  - files changed against the merge-base with `origin/<base>` (`git diff --name-only --no-renames -z`), and uncommitted files (`git status --porcelain=v1 -z`, both paths of renames);
  - new files under the decision-record folder, to flag duplicate numbers.
- Per worktree, its records with the usual `records_dir()` rule: the newest route's required steps and controls, and evidence with `head`.
- `reviews.jsonl` and `land.jsonl` under `<git common dir>/susanoo/records/`. A `land` run matches when the branch tip equals a candidate's recorded tip or is an ancestor of the commit `land` pushed.

**Columns.**

- **Checks:** `verified@<sha>`, `stale (n commits)` when the newest passing evidence is for an older commit, `unverified`, or `failed`.
- **Review:** the newest review for the current tip, or `-`.
- **Land:** the newest matching `land` result (`ready`, `failed`, `conflict`, `merged`), or `-`.
- **Marks:** `security` when the route has a security control (the lead then also runs `/security-review`); `overlap` with the shared paths and the other branch; duplicate ADR numbers. Shared paths under `[[land.keep_both]]` (read from `land`'s own configuration) are shown as "append-only" rather than overlap, because `land` may resolve them.

**What it never does.** Write to the repository, its index, or any record; fetch; run checks; read transcripts; delete anything. It does not predict textual conflicts, because `git merge-tree --write-tree` writes objects; `land` does that. Text from records is cut to one line and shown as data.

## Measuring it

Fixed before any data exists, and evaluated in the real-session analysis planned for late October 2026, from the user's own sessions. The baseline is the user's sessions on this repository from 2026-10-01 to the day the lead skill is first used; the comparison is the two weeks after.

- User messages per merged pull request.
- User messages that only relay between sessions: asking one chat to check another's work, or carrying a pull request number or output from one chat to another.
- Conflicts the user resolved by hand.
- Tokens per merged pull request, lead and workers together, with the usage capture from #52.

If relay messages per merged pull request have not fallen after two weeks, or the user goes back to talking to workers directly, the lead skill is withdrawn; the evidence `head` field, review records, and board stay, because `land` uses them. These are counts with stated sample sizes, not validated thresholds.

## Consequences

- The user talks to one chat; starting, watching, and messaging workers is done with the app's own features.
- More sessions and pull requests: every change becomes a worker and a pull request. The lead may give one worker several small requests in the same area.
- The main checkout becomes the lead's and stays on the base branch. On this repository today it is on a feature branch, so adopting the workflow starts by moving that work into its own worktree.
- If the app's session features change, the lead degrades to printing worker briefs for the user to start. The board, reviews, and `land` do not depend on the app.
- New code, all of it checks: the evidence `head` and `dirty` fields, `susanoo review record` and its gate in `land --merge`, `susanoo board`, and `lead on`/`off` in the prompt hook. New skill text: `susanoo-lead`. No orchestration code, no polling, no new long-running state.

## Slices

Accepted on 2026-10-15 and built in this order, because each later slice reads what the earlier ones record:

1. **Evidence bound to a commit, and review records.** `head` and `dirty` on evidence, the "verified" helper, `susanoo review record` and `list`. Implemented (unreleased).
2. **The review gate in `land --merge`** (`[land] require_review`), and, for "verified", a review record for the tip in place of a reported review step. Implemented (unreleased); the gate requires a `code-review` only (see "Reviews").
3. **`susanoo board`.** To build.
4. **`susanoo lead on`/`off` and the `susanoo-lead` skill**, with the worker rules. To build.

## Open questions

1. How much the lead's findings in a brief raise a worker's routing floor, checked with `susanoo route` on sample briefs before the skill ships. If too much, the findings move to a file the brief points to.
2. Whether `/code-review` can be pointed at a pull request or branch from the lead's checkout and read the branch's files, or needs the worker's worktree path.
3. Whether Claude Code's agent teams can run workers that land through pull requests; if so, the skill could use them in the CLI.
4. Whether the shared files should go instead of being resolved: changelog fragments, ADR numbers taken at landing, generated files rebuilt once after landing. Separate work.
5. Whether the lead may make one-line fixes itself in a short-lived worktree, rather than paying for a full worker and pull request each time.
