# 0013: Leases, interface-change notices, and an evidence-gated landing queue

**Status:** proposed (draft for review). Nothing here is implemented; every command and file marked *proposed* is new work. Builds on [0012](0012-delegation-plans.md). Phases 1 to 3 are built only if the phase 0 measurement shows they are needed. Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0007](0007-hands-off-by-default.md), and [0009](0009-clarifying-questions-under-hands-off.md). If accepted, it amends 0007's list of prompts that stay in every mode (see "Applying a landing"). Example: [`0013-coordination-state.example.json`](0013-coordination-state.example.json).

## Context

ADR 0012 gives each delegated unit a merge verdict: its evidence is fresh, and its diff stays inside the files it owns. But each verdict checks a unit on its own base. Merging several mergeable units can still break the result:

- **Semantic conflicts.** Agent A renames a function; agent B adds a call to the old name. Each branch passes its checks alone, git merges them without a conflict, and the combined tree is broken. Git sees text, not meaning.
- **Late discovery.** The break shows up in CI after push, or on the next pull, far from the agent that caused it. GitHub's merge queue tests combined changes, but only after push, and it knows nothing about agents, plans, or which unit owns what.
- **No plan at all.** Ad-hoc parallel sessions (several Claude Code sessions on one repository, as is common) have no plan, so ADR 0012 gives them nothing.

Running agents and letting them talk to each other (Claude Code's agent teams, Scion's hub) are crowded areas that the platforms are absorbing. None of the agent runners named in ADR 0012 does **landing**: combining N agent branches into a tree that is still correct, with proof that comes from outside the model. Susanoo can do that without becoming an orchestrator (ADR 0003). It never runs agents; it decides what may land.

How often semantic breaks happen in practice is not known. Building all of this on a guess would be the kind of process the architecture's first principle warns against.

## Options

1. **Rely on CI and the remote merge queue.** No new work. Breaks are found after push, are attributed to a pull request rather than to an agent, and ad-hoc local sessions get nothing.
2. **Leases only.** Agents see who is working on what, and avoid it. Cheap, but avoidance cannot prove the combined tree works.
3. **A landing queue only.** It checks every combination and catches breaks, at the cost of a check run per branch.
4. **Measure first, then build leases, notices, and the queue as a fixed rule decides.** Chosen.
5. **A standalone, harness-agnostic tool now.** Premature before anything shows it is needed.

## Decision

Option 4.

These build on the plan format of ADR 0012 but also work without a plan, for ad-hoc parallel sessions. Formats are sketched in [`0013-coordination-state.example.json`](0013-coordination-state.example.json).

### Shared state

- **Location.** `<git common dir>/susanoo/`, found with `git rev-parse --path-format=absolute --git-common-dir` (git 2.31 or later; without `--path-format`, the main worktree gets a relative path). Every worktree of a repository sees the same common directory, it is never committed, and it needs no server. Clones, and containers that do not mount it, do not see it (open question 1). Hooks cache the path in their per-session state rather than running git on every call.
- **Writes.** A writer creates `susanoo/lock` exclusively (`O_CREAT | O_EXCL`) with a random nonce, its process id, and the time. It reads the file, writes a temporary file in the same directory, and moves it into place with `os.replace`. Slow work, such as scans and checks, happens outside the lock.
- **Stale locks.** A lock older than 30 seconds is stale. A writer breaks it by renaming it to a unique name and then reading the renamed file. If the nonce is not the one it judged stale, it has taken another writer's live lock: it puts the file back with a rename that fails if a lock already exists (`os.link` on POSIX, `os.rename` on Windows), and it retries from the start. Every holder re-reads its own nonce in the lock file just before `os.replace`, and aborts without writing if the nonce changed.
- **Hooks and readers.** Hooks try the lock once and skip their write on contention; they never wait. On Windows, `os.replace` and `open` can fail briefly while another process holds the file, so readers and writers retry a few times.
- **Notices.** One file each, never rewritten, so they need no lock.
- **Trust.** Any worktree, and so any agent, can write this state, including `refs/susanoo/*`. It is advisory. Anything that moves the base checks the coordinator's own records (below), never `queue.json` or a ref alone.

### Phase 0: measure first (the kill criterion)

Before phases 1 to 3 are built, Susanoo measures how often they would have mattered, on the user's real parallel work. Phase 0 needs much of phase 3's machinery: temporary worktrees, combined runs, and the evidence `head` field proposed in ADR 0012. What the kill criterion can save is leases, notices, blast radius, and a queue that applies anything. It cannot make phase 0 itself cheap.

- *Proposed:* `susanoo land --measure <branch>...`.
  - It moves no branch.
  - It does write git objects, a temporary worktree, and a `landings` record.
  - It runs the project's checks, so it executes agent-written code on the host, as `--reverify` in ADR 0012 does. It is an explicit command, and its output says so.
- **Episode.** An episode is two or more agent branches pending at the same time against one base tip. A measurement is identified by (base tip, set of branch tips), so rerunning it on the same tips adds nothing. A semantic break counts once per episode and set of branches involved, so the same break measured at two later tips counts once.
- **Textual conflicts.** These count only between agent branches: `git merge-tree --write-tree` (git 2.38 or later) for each pair, once per pair per episode, ignoring conflicts only in `coordinator_owns` paths. Conflicts between a branch and the base measure staleness, not interaction; they are reported separately and do not count. A conflict that appears only when three or more branches are combined is recorded as a textual conflict for the episode.
- **Semantic breaks.** A break is counted only when every one of these happens in the same temporary environment:
  1. the base tip passes alone;
  2. each branch passes alone on that base tip;
  3. the combination merges without a textual conflict;
  4. the combination fails;
  5. the failure repeats once on a rerun.

  Anything else is recorded, but not as a break. A failure can come from drift in the base, a flaky check, or a missing environment rather than from agents interacting.
- **Decision rule.** The rule below is fixed in this ADR before any data exists. Each `landings` record stores the rule's version, so a later change of configuration cannot reinterpret the data. The rule is evaluated after at least 30 distinct measurements from at least 5 episodes:
  - **No semantic breaks:** build no notices and no blast radius. The queue stays a dry-run report (`land --measure`).
  - **One or more semantic breaks:** build the landing queue, phase 3.
  - **Two or more semantic breaks:** also build leases and notices, phases 1 and 2. Notices need leases to find the affected sessions.
  - **Three or more textual conflicts between agent branches, outside coordinator-owned files:** build leases (phase 1), even with no semantic breaks.

  These are counts, not rates, and they form a stopping rule, not a validated threshold.
- **Anecdote, not data.** While this ADR was being written, four sessions worked on this repository in parallel, and most were likely to touch `CHANGELOG.md` and `docs/roadmap.md`. That is the case `coordinator_owns` exists for. It is also a reason to keep each session's edits to such files small.

### Phase 1: leases

A lease says which files a worktree is working on, so overlap shows up while agents work, not at merge time.

- **Keyed by worktree, not session.** Subagents share their parent's session id, and probably its project directory (ADR 0012, fact 3). The hook maps each edited path to its worktree (`git rev-parse --show-toplevel`, cached per directory) and records it on that worktree's lease. Bash and PowerShell calls carry no file path, so for them the hook uses the event's working directory. The session id is kept for information only.
- **Owned files.** These come from the plan unit when there is one. Otherwise they are declared (*proposed:* `susanoo lease claim <path>...`) or **observed**: paths the worktree's sessions have edited. PreToolUse fires before Claude Code's permission decision, so an edit the user denied can still be recorded as observed. Observed paths are a hint, not proof.
- **Blast radius.** This is a set of files outside the lease that depend on what it owns, computed by a read-only scan with the standard library:
  - Python uses `ast` on import statements.
  - JavaScript and TypeScript use a regular expression for relative `import`, `export ... from`, and `require`; this is approximate.
  - Other languages get none, and the lease says so.

  It is computed two ways:
  - **At claim time, as a prediction.** The scan reverses the import graph of the base, one level deep (the depth is data).
  - **At `notice scan` and `land` time, as an observation.** It scans the imports of each other active lease's own files, at that lease's worktree. This is the case that matters: agent B adds a new import of A's file on B's branch, which no scan of A's tree or of the base can see.

  The scan executes nothing and walks within the same limits as discovery. It never runs inside a hook. It is a prediction and is never described as validated.
- **Overlap is reported, not blocked.**
  - Lease X is `contested` with lease Y when their owned or observed files overlap. The claim still succeeds.
  - X is `at_risk` from Y when one of X's files imports a file Y owns or has observed. Y's blast radius then contains that file of X.
  - `susanoo lease status` lists both kinds of overlap. The hook tells the affected worktrees once per path.
- **Lifecycle.**
  - **Claim:** when a session routes a task (with `--plan`, or at its first edit, without a scan), or by an explicit claim. The claim records `base_at_claim`.
  - **Heartbeat:** the hook refreshes the heartbeat at most once every five minutes. It flushes buffered observed paths in the same write.
  - **Release:** only when the branch's landing is applied, when `delegate merged` records it, or by `susanoo lease release`. Closing a route does not release a lease, because an unlanded branch still holds its files.
  - **Stale:** a lease is stale when it has had no heartbeat for `lease_ttl` (data, default two hours), when its worktree no longer exists, or when its tip differs from `base_at_claim` and is already contained in the base. The last test means a branch with no commits yet is not stale. A heartbeat revives a stale lease, and its overlap is checked again and reported. Stale leases do not count for overlap, but stay listed until `susanoo lease prune`.
- **The hook warns by default.** When a session edits outside its worktree's lease, the PreToolUse hook allows the edit and adds a note: who holds the path, or that nobody does. It does this once per path per lease.
  - The setting is `[leases] outside = "warn" | "deny" | "off"`, default `"warn"`, following ADR 0007 and ADR 0009: no questions, and blocking only by deliberate configuration.
  - Shell commands that write files are not seen. For plan units, the merge verdict's diff check catches them. For ad-hoc sessions, only the combined run at landing does.
- **Hook cost.** The hook already reads the route records on every call. Leases add only:
  - a cached path lookup;
  - a read of the worktree's lease (each hook is a new process; a cached copy in the hook state is refreshed at heartbeat time);
  - a `git rev-parse --show-toplevel` subprocess when the per-directory cache misses;
  - a write, at most once every five minutes, of buffered paths, the heartbeat, and a listing of the notices folder. The buffer is kept per worktree, because subagents share their parent's session state.

### Phase 2: interface-change notices

When a branch changes something other worktrees depend on, they should hear about it before landing, not from a broken build.

- **Detection.** *Proposed:* `susanoo notice scan`. The unit brief asks agents to run it after committing, and `land` runs it anyway. For each file F changed on lease Y's branch (merge-base to tip), lease X is affected when:
  - one of X's owned or observed files imports F, at X's worktree; or
  - X's plan unit lists F in `reads`, or consumes an interface located in F.

  Python files are compared at both ends with `ast`, giving the top-level functions and classes removed, renamed (one removal plus one addition with the same parameters), or with changed parameters. Other files are reported as changed files only.
- **Record.** One file per notice, `<common dir>/susanoo/notices/<notice id>.json`, with the changing lease, the files and symbols, the affected leases with the reason, and the time. An affected worktree acknowledges a notice by creating `<notice id>.ack.<lease id>`, so no shared file is rewritten.
- **Delivery.** The plain-file adapter is the default and needs no other tool: an affected worktree's hook shows unacknowledged notices as context, once each, at its next heartbeat or prompt. Other adapters run only from an explicit command (*proposed:* `susanoo notice send --via scion|mailbox`), never from a hook, because `scion message` may contact Scion's server:
  - a Claude Code agent-teams mailbox, if it can be written from outside a team;
  - `scion message <agent>`.

  Notices are hints. The landing queue is still the check.

### Phase 3: the evidence-gated landing queue

*Proposed:* `susanoo land`. Branches land one at a time on the local base branch, and each one is checked on the combined tree, before anything is pushed. GitHub's merge queue does a similar job after push. This one is local, and it knows the plan, the leases, and the evidence.

- **Queue.** `<common dir>/susanoo/queue.json` holds entries in order: the plan's merge order when there is a plan, otherwise first come, first served (`susanoo land enqueue <branch>`). `susanoo land drop <branch>` removes an entry.
- **Chain.** Entries that pass build on each other. The queue records the base tip it started from (the chain root), and each entry records the commit it was built `onto`. Applying an entry moves the base onto that entry's landing commit, which keeps the chain intact. The chain breaks in two cases, and then every entry after the break that is not yet applied is invalidated and rebuilt on the next run:
  - the base tip is not the chain root or one of the chain's landing commits, for example after a direct commit to `main` or a `git pull`;
  - an earlier entry is dropped.

  Susanoo never sees a declined ask, so declining a landing means running `land drop <branch>`. Until then, later entries wait. Rebuilding reruns their checks unless the cache below already covers them.
- **One step per run.** `land --next` processes one entry and exits. Each entry moves through stored states: `queued`, `building`, `landed` or `bounced`, then `applied`. A run that is killed (Claude Code's Bash tool stops commands after at most ten minutes) leaves a `building` entry, which the next run resets. That run also removes orphaned temporary worktrees. Nothing runs in the background.
- **Processing an entry.** Like `land --measure` and ADR 0012's `--reverify`, this runs agent-written code on the host, outside any container the agent used. `land` is an explicit command, and its output says so.
  1. **Skip held entries.** An entry is skipped, and held, when its history contains a bounced tip or one of its `after` units bounced.
  2. **Check the plan verdict.** When the entry belongs to a plan, its merge verdict must pass: ownership, and evidence bound to the tip.
  3. **Build the combined commit without a worktree.** `git merge-tree --write-tree` onto `onto`, then `git commit-tree`, using the user's configured identity. A textual conflict bounces the entry with the conflicting files.
  4. **Check out a temporary detached worktree** at the combined commit, with repository hooks disabled (`-c core.hooksPath=`). Run `[land] setup` (for example `pip install -e .` into a temporary environment, or `npm ci`), because a fresh worktree has no installed dependencies. Without setup, a Python editable install from the main checkout would test the wrong code. The worktree is removed in every case, even after an error.
  5. **Run the runnable steps.** These are the union of the unit's required steps, the parent route's runnable steps, and `[land] verification`. Configuration and the tool registry come from the applied base, never from the branch, so a branch cannot rewrite the commands that check it. `.susanoo/` is in the default `coordinator_owns`, and a branch that changes it is flagged. Reported steps (reviews, visual checks) cannot be rerun. Only the unit's own reported steps are taken from its tip-bound evidence. Reported integration steps, such as a review of the full merged diff, stay with the parent route and are done once at the end.
  6. **Pass:** the entry becomes `landed`, its combined commit is stored on `refs/susanoo/landed/<base>`, and a `landings` record in the coordinator's records names that exact commit.
  7. **Fail:** the failing step is rerun once to rule out a flaky check. If it fails again:
     - the branch alone is run on the last *applied* base (one extra run, only on failure);
     - if that also fails, the failure is attributed to the branch ("broken alone");
     - if it passes, the failure is attributed to its interaction with the entries landed since then, which the record lists with their notices and lease overlaps.

     The entry is `bounced` and its session is told through the notice adapters. Its lease stays active, and the queue continues from the last landed commit.
- **Cache.** A result is reused only when all of these match: the tree id, the exact step commands, a hash of the configuration and registry, and an environment fingerprint (the setup commands, the interpreter and tool versions, and the lockfile hashes). Only passing results are cached, and `--no-cache` bypasses the cache. A tree id alone does not decide a result: external services and flaky checks can still differ, which is why failures are never cached.
- **Integration evidence.** When the last entry of a plan is applied, the executed results of its landing run count as evidence for the parent route, because their `head` is the applied commit. Reported integration steps still need to be done.

### Applying a landing

- *Proposed:* `land --apply <branch>` applies exactly one entry, and only when:
  - every earlier entry is already applied;
  - a passing `landings` record in the coordinator's records names that entry's exact landing commit.
- **How the base moves.** In the worktree where the base is checked out, it runs `git merge --ff-only <landing commit>`, and refuses if that worktree has uncommitted changes or the move is not a fast-forward. If the base is not checked out anywhere, it uses `git update-ref` with the old value as a check. It then releases the lease and records the merge, as `delegate merged` does.
- **Always asks.** The PreToolUse hook asks the user before `land --apply`, in every mode, like `susanoo approve` in ADR 0007, so each branch lands with the user's say-so. It also asks before git commands that move the base to a commit under `refs/susanoo/landed/` (`git merge`, `git reset`, or `git update-ref` naming such a ref or its commit). Accepting this ADR adds these to ADR 0007's list of prompts that stay in every mode.
- **No push.** `land` never pushes. After landing, the user pushes, and the remote CI or merge queue runs as usual.
- **Not in version 1:** batching several branches and bisecting failures, as large merge queues do.

### Standalone product?

Leases, notices, and the queue do not depend on Claude Code. They could become a separate, harness-agnostic tool. The recommendation is to build them inside Susanoo first, under the same stdlib-only and read-only rules, and to extract them only if the phase 0 measurement and real use justify it.

### What Susanoo will not do

- Move the base branch itself without asking the user. Plain git can still move it outside the patterns the hook recognizes; as in ADR 0007, this is a guardrail, not a security boundary.
- Run a long-lived process, poll agents, or push. Each command does one thing and exits (ADR 0003).
- Describe the blast radius, the notice detection, or the kill thresholds as validated.
- Read anything under `.scion/` or any credentials; the rules of ADR 0012's discovery section apply unchanged.

## Consequences

- Leases, notices, and the queue add Susanoo state inside `.git` (the common directory). It is local, never pushed, and removed with the clone. `susanoo doctor` should show it and `susanoo lease prune` should clean it.
- The landing queue runs the project's checks at least once per branch on the combined tree, plus setup in a fresh worktree, plus one rerun and one solo run per failure. That is the real cost, and the reason for the cache and for measuring first.
- If phase 0 finds no semantic breaks and fewer than three textual conflicts between agent branches, phases 1 to 3 shrink to a dry-run report. The kill criterion is part of this decision, not an afterthought.

## Open questions

1. Leases and notices in containers and clones. Scion mounts the repository's `.git` into its containers, so local Scion agents should see the common directory. Under Docker some `.git` paths are mounted read-only or narrowed, which needs checking. Clone-mode workspaces (Scion's remote hubs) have their own `.git`, and would need an exported lease file or the git-notes path from ADR 0012's open question 5.
2. Whether Claude Code's agent-teams mailbox can be written from outside a team (from a hook or the CLI); if not, that adapter is dropped.
3. Whether the Python blast-radius scan should also follow `from package import name` through `__init__.py` re-exports, which is more accurate and slower.
4. Whether leases, notices, and landing should later become a standalone, harness-agnostic tool. The current answer is no, until measurement justifies it.
5. `[land] setup`: how to build a correct environment in a fresh temporary worktree for each ecosystem (Python editable installs, `node_modules`, compiled languages) without network surprises or long setup times. This needs a spike before phase 0, because phase 0 depends on it.
6. Whether `land` should also ask Claude Code's agent teams or Scion to pause the affected agents while their branch is bounced. The current answer is no: the bounce is a notice, and agents keep working.
