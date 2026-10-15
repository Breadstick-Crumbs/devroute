# 0015: A lead session per repository, and a read-only `susanoo board`

**Status:** proposed (draft for review). Nothing here is implemented; every command, skill, and file marked *proposed* is new work. The number is provisional: [0012](0012-delegation-plans.md) and [0013](0013-leases-and-landing-queue.md) are proposed on the `delegation-plans-adr` branch and [0014](0014-susanoo-land.md) is being built, so the links resolve and the number is checked again when this lands. Builds on 0013's landing design and 0014's `susanoo land`, and amends one sentence of 0014 (see "Merging"). Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), [0007](0007-hands-off-by-default.md), and [0009](0009-clarifying-questions-under-hands-off.md).

## Context

The user runs several Claude Code sessions on one repository at once, each in its own worktree and pull request. Running them is solved. Working with them is not. On this repository in October 2026:

- **The user is the message bus.** One session was put "in charge of merges", and the others had to wait for its go-ahead. Brief wording was "agreed with" another session by the user carrying text between chats. Asking one chat to check another chat's work was a manual step every time.
- **Nobody sees the whole picture.** Seven worktrees existed besides the main checkout; three of them were already merged and still checked out. The main checkout itself was on a feature branch. Which branch touched which files, which had passing evidence, and which was waiting on what, was only in the user's head.
- **Shared files collide.** `CHANGELOG.md`, `docs/roadmap.md`, the ADR index, and ADR numbers changed in several branches at once. The delegation ADR was drafted as 0011 and had to be renumbered because another session took 0011 first.
- **Merging was hand work.** 0014's `susanoo land` replaces it, but `land` starts when the branches are done. It does not help decide who works on what, or notice two sessions heading for the same files.

0012 plans the top-down split of one large task into units. The user's work mostly looks different: many small, independent requests ("fix this", "fix that") arriving while earlier ones are still running. 0013 named a "board" and "intents" for this case but did not design them, and builds leases only when measured semantic breaks or textual conflicts call for them. The cost the user feels is coordination, which neither count measures.

The Claude desktop app, where the user works, lets one session start other sessions in their own worktrees, receive a note when each one finishes a turn (quoting the start of its report), and send them follow-up messages. These are app features, not a documented interface. They are described here as observed in October 2026 and are rechecked before anything depends on them. Claude Code's CLI has worktrees (`claude --worktree`), subagents with `isolation: worktree`, and experimental agent teams.

## Options

1. **Keep going as now.** The user starts sessions, relays between them, and decides when to merge. No work; the problem stays.
2. **Susanoo as orchestrator.** Susanoo starts sessions, polls them, and merges. Rejected by ADR 0003: it duplicates what the app and Claude Code do and needs a long-running process.
3. **Scion or another container runner.** Containers isolate agents, which worktrees already do; they do not coordinate them. The Windows setup cost recorded in the draft 0012 makes this worse.
4. **Claude Code's agent teams.** A lead with teammates and a shared task list. Experimental and CLI only, and whether teammates get their own worktrees and branches that land through pull requests is not established (open question 4). It could become another way to run workers later; it does not replace the board.
5. **One lead session per repository, using the app's linked sessions to run one worker session per task, with Susanoo supplying a read-only board and the lead's instructions as a skill.** Chosen.

## Decision

Option 5. The user talks to one session, the **lead**. The lead never edits tracked project files; it hands every change to a **worker** session, watches the workers through `susanoo board`, has each finished worker's work reviewed, and lands it with `susanoo land`. The user is asked one thing per batch: merge these pull requests? Susanoo still runs no agents (ADR 0003). The app runs them; Susanoo supplies the facts the lead decides from and the rules it follows.

### Roles

- **Lead.** One session per repository, in the main checkout. That checkout stays on the base branch, clean and fast-forwarded to the remote base, because the lead reads the code there and the app starts workers from it. The lead answers questions itself (reading is fine) and turns every request that would change files into a worker. It is the only session that messages workers, assigns ADR numbers, and writes the project's state notes (such as Claude's project memory).
- **Worker.** One session per task, in its own worktree and branch, started by the lead from the freshly updated base. It routes and verifies as any session does today: the brief and the evidence gate apply in its worktree, provided Susanoo's hooks are active there (global hooks, or project hooks committed to the repository; hooks in a git-ignored `settings.local.json` are not copied into new worktrees). It ends by pushing its branch and opening a pull request. It asks the lead, not the user, when it is blocked; the lead asks the user only when it cannot answer itself (ADR 0009).
- **User.** Says what they want to the lead, answers clarifying questions, and approves merges. They can still talk to a worker directly; that work still lands through the lead.

### The lead's ledger

Some of the lead's state is not in git or in any record: requests queued behind a running worker, the files the lead expects each worker to touch, ADR numbers handed out, and the review round each worker is in. The lead keeps these in a *proposed* ledger, `<git common dir>/susanoo/lead.md`: a short Markdown file the lead writes and rewrites, outside the working tree, never committed, shared by every worktree of the repository. Susanoo does not write it and does not parse it; the board prints its path and age. With the ledger and the board, a fresh lead session can take over from one whose context has grown too long.

### The lead's loop

The loop is instructions in a *proposed* `susanoo-lead` skill, installed by `susanoo init` next to the existing `/susanoo` skill (ADR 0003: a skill, not a process). It is text for Claude, so it can be followed in the CLI too, with the user starting workers by hand.

1. **Intake.** Read `susanoo board --prs` and the ledger. If an active worker already owns the area, send the request to that worker as a follow-up, unless its pull request is in a `land` run. If an active worker's changed files overlap the files the request will likely touch, queue the request in the ledger until that worker lands, or give it to that worker. Otherwise start a new worker.
2. **Dispatch.** Fetch the base and fast-forward the main checkout to it. Start the worker in its own worktree with a brief whose **first line is the user's request in their words**, followed by what the lead found when reading, the files the lead expects it to touch, and an ADR number if it needs one. The worker rules are not pasted into the brief; it names the skill section that holds them (see "Routing"). Record the worker in the ledger. Several workers run at once only when their expected files do not overlap.
3. **Wait.** Do not poll. The app tells the lead when a worker finishes a turn. A note is a claim, not proof.
4. **Check.** Read the board row for the worker's branch; it must be `ready`. Then have its diff reviewed by an independent reviewer (the `susanoo-reviewer` subagent, run from the lead, given the pull request's diff and the worker's worktree path, so it reads the branch's files rather than the base's). Findings go back to the worker as a message, and step 3 repeats.
5. **Land.** When one or more workers pass, run `susanoo land <pr>...` without `--merge`. A failed or conflicting run goes back to the culprit worker, with `land`'s attribution.
6. **Merge.** See "Merging": one question, then `land --merge`.
7. **Tidy.** After a merge, fast-forward the main checkout, apply queued requests from the ledger, and clean up. The board lists worktrees whose branch was merged; the lead asks the app to archive those worker sessions, or prints the cleanup commands for worktrees it did not create. It deletes nothing itself.

### Merging

0014 asks the user, through the PreToolUse hook and in every mode, before any `susanoo land ... --merge`. That prompt is the merge question; the lead does not also ask in chat. The lead posts one line naming the pull requests that are ready and what each does, then runs `susanoo land <pr>... --merge`, and the user's answer to the prompt is the approval. This amends 0014's "`--merge` is a flag the user gives. An agent never passes it on its own." to: an agent may run it only through that prompt, so the user still gives every merge, once.

### Routing

Two things must not happen: the lead's own requests routed as work it will not do, and the worker rules routed as part of every task.

- **The lead session is not routed.** Under automatic routing, every message the user sends the lead would get a brief telling it to investigate, plan, and verify, and would leave a route in the main checkout's records that never gets an outcome, beside the worker's own route for the same task. *Proposed:* `susanoo lead on`, run by the lead skill, marks the current session as a lead in the hook state, keyed by its session id. Its prompts are then not routed; the hook adds one short line instead (the worker gets routed). `susanoo lead off` undoes it. This is a new code path in the prompt hook, and the only Susanoo state this ADR adds.
- **The worker routes its first line.** The worker's first message is the brief, and automatic routing scores the whole message. The worker rules (CHANGELOG, roadmap, merge, push wording) would add signals to every task and raise every floor, and floors only go up. So the rules live in the skill, and the brief carries the task first and the lead's findings after it. Whether the findings themselves raise floors more than they should is checked with `susanoo route` on sample briefs before the skill ships (open question 2).

### Worker rules

Held in the skill, so they are data, not code.

- Change only what the task needs. If the task turns out to need files another worker is likely changing, tell the lead before editing them.
- Use the ADR number the lead gave, or ask for one. Do not renumber ADRs.
- Append to `CHANGELOG.md` under *Unreleased* only, without adding headings or editing other entries; that is what 0014 can resolve. Edit only your own rows of `docs/roadmap.md`, as AGENTS.md already asks; git merges edits to different rows cleanly.
- Do not merge, rebase onto, or edit another worker's branch. After a `land` run has pushed your branch, run `git merge --ff-only origin/<your branch>` before more edits, or your next push is rejected.
- Finish in this order: commit, `susanoo verify --execute`, push, open a pull request. Evidence counts only when recorded after the last commit, so a later commit needs verification again.
- End the turn with a report whose **first line** is the status, because the lead may see only the start of it:

  `READY pr=#57 branch=lead-board verify=pass files=4` or `BLOCKED <one-line reason>` or `NEEDS-INPUT <one-line question>`.

### `susanoo board`

*Proposed:* `susanoo board [--base <branch>] [--prs] [--json]`. One line per worktree of the current repository, terse enough to read on every intake: a board of eight worktrees should be well under 2 KB of text.

**What it reads.** Everything below is local except `--prs`:

- `git worktree list --porcelain`: each worktree's path, branch or detached `HEAD`, and whether it is locked or prunable.
- Per worktree, with `git --no-optional-locks -c core.fsmonitor=false`, so that no command refreshes another worktree's index or starts a file-system monitor:
  - ahead and behind counts against `origin/<base>` as last fetched (the board never fetches);
  - files changed on the branch, `git diff --name-only --no-renames -z <merge-base> HEAD`, so a move lists both paths;
  - uncommitted files, `git status --porcelain=v1 -z`, including both paths of renames and copies, ignored files left out;
  - whether the tip is pushed (its upstream contains it);
  - new ADR files the branch adds (`docs/decisions/NNNN-*`, the folder from architecture data), so that two branches taking the same number are flagged.
- Per worktree, its Susanoo records with the usual `records_dir()` rule (project or global mode): the newest route (id, profile, time, session id, and the task text when `store_task_text` allows it, cut to its first line), its newest outcome, and its evidence. **Verified** means every required step of the newest route has its newest evidence `executed` and passing, recorded after the branch's last commit; reported evidence is shown but does not count. Until 0012's `head` field exists, "after the last commit" compares times, and the board marks it as approximate.
- `land`'s records (`<git common dir>/susanoo/records/land.jsonl`, 0014), when they exist: the newest run that names this branch, matched when the worktree's `HEAD` equals a candidate's recorded tip, or is an ancestor of the commit `land` pushed. `land` merges the base into a candidate before pushing, so the pushed tip is not the worktree's `HEAD`.
- The modification time, never the content, of the route session's Claude Code transcripts (the main transcript and its subagents' transcripts, found as in `usage.py`), as "active 4m ago". Transcripts are read for content only by the opt-in usage capture, which the board does not use.
- With `--prs` only: `gh pr list --state all --json number,headRefName,state,isDraft` for recent pull requests, run with `GH_NO_UPDATE_NOTIFIER=1`. This is a network call, so it is opt-in; the lead skill passes it.

**The main checkout** is listed first and gets no state. It is flagged when it is not on the base, not clean, or behind `origin/<base>`, because the lead reads code there and the app starts workers from it. The ledger's path and age are shown with it.

**States** for every other worktree, by fixed rules, the first that matches:

| State | Rule |
|---|---|
| `scratch` | the path starts with `land`'s temporary-worktree prefix: left out of the board unless `--all` |
| `missing` | git reports it prunable, or its path does not exist: no other check runs |
| `merged` | a `land` run or a merged pull request (with `--prs`) names the branch, or its tip is in `origin/<base>` but not on its first-parent line, which is where a merged branch's commits sit; and nothing is uncommitted |
| `new` | no commits beyond the base, and nothing uncommitted (a worker that has just started, or one that is only reading) |
| `failed` | the newest matching `land` run failed or hit a conflict on this branch |
| `working` | uncommitted changes, or the session was active in the last 15 minutes (data) |
| `unverified` | committed, but not verified after the last commit, and no recent activity |
| `ready` | verified, pushed, and its pull request open; without `--prs` the last condition is not checked and the state is shown as `ready?` |
| `idle` | anything else, for example verified but not pushed |

Detached and locked worktrees get the matching state with a `detached` or `locked` mark. A worker that is fixing a failed verification shows as `working`, not as stuck, because activity is checked before verification.

**Overlaps.** Every pair of worktrees in `new`, `working`, `unverified`, `ready`, `idle`, or `failed` whose changed or uncommitted files intersect, with the shared paths. Paths under `[[land.keep_both]]` (read from the same configuration `land` uses, so the two cannot drift) are listed separately as "append-only, land may resolve": `land` resolves them only when both sides purely added lines without headings. Every other shared path is a real overlap. The board also lists worktrees where Susanoo's hooks are not active, using the same check as `susanoo doctor`.

This is the cheap half of 0013's leases: overlap observed from the branches themselves, with no hooks and nothing to keep fresh. It cannot see a file a worker is about to edit, only ones it has edited; the lead's expected-files list in the ledger covers the rest. 0013's leases stay gated by its own rule.

**What it never does.** Write to the repository, its index, or any Susanoo record; fetch; run project checks; read transcript content; delete or prune worktrees. (`gh` may keep its own cache outside the repository.) It prints cleanup commands for `merged` worktrees and runs none. It does not predict textual conflicts, because `git merge-tree --write-tree` writes objects; `land` does that.

Output is Markdown, or JSON with `--json` for other tools. Text taken from records was written by an agent or the user, so it is cut to one line and shown as data.

### Where this differs from 0012 and 0013

- **No plan document.** Workers start from the user's requests as they come. 0012's plans stay the right tool for splitting one large task.
- **The board replaces 0013's undesigned board.** Observed overlap replaces leases for now. Intents (a session declaring files before editing) are the expected files in the lead's ledger, kept by the lead, not by a hook.
- **0013's notices** are the lead's messages to workers, sent through the app.

### Measuring it

Fixed before any data exists, and evaluated in the real-session analysis planned for late October 2026 from the user's own sessions, never from synthetic runs. The baseline is the user's sessions on this repository from 2026-10-01 to the day the lead skill is first used; the comparison is the two weeks after.

- User messages per merged pull request.
- User messages that only relay between sessions: asking one chat to check another's work, or pasting a pull request number or a session's output from one chat into another.
- Conflicts the user resolved by hand.

If relay messages per merged pull request have not fallen after two weeks, or the user goes back to talking to workers directly, the lead skill is withdrawn and the board stays. These are counts with stated sample sizes, not validated thresholds.

## Consequences

- The user talks to one chat. Starting workers, checking them, review, and landing are the lead's job; the user answers task questions and approves each merge batch once.
- More sessions and more pull requests: every change, however small, becomes a worker and a pull request. The lead may give one worker several small requests in the same area to keep this down.
- The main checkout becomes the lead's: it stays on the base branch. On this repository today it is on a feature branch, so adopting the workflow starts by moving that work into its own worktree.
- The workflow depends on app features that are not a documented interface. If they change, the lead degrades to printing worker briefs for the user to start; the board still works, because it reads git and records, not the app.
- New code: the read-only `board` command, the `susanoo-lead` skill, and `lead on`/`off` with its check in the prompt hook. The ledger is written by Claude, not by Susanoo. No new record kinds.

## Open questions

1. Whether the app's session-to-worktree mapping is stable enough for the board to show which app session owns which worktree, beyond the route's session id. A worker with no route yet has no session id, and so no activity time.
2. How much the lead's findings in a brief raise a worker's floor, checked with `susanoo route` on sample briefs before the skill ships. If too much, the findings move to a file the brief points to.
3. Whether a review should count for `land` only when it is recorded against the exact tip (it needs 0012's `head` field), and where that record lives: the lead's records or the worker's.
4. Whether Claude Code's agent teams can run workers in their own worktrees and land them through pull requests; if so, the skill could use them in the CLI.
5. Whether the shared files should go instead of being resolved: changelog fragments per branch, ADR numbers taken at landing, generated files (`docs/assets/demo.svg`, the `.claude/` template copies) rebuilt once after landing. Most "append-only" rows would disappear. It is separate work.
6. Whether the 15-minute activity window and the 2 KB size goal hold up on real boards.
7. Whether the lead may make one-line fixes itself in a short-lived worktree, rather than paying for a full worker and pull request each time.
