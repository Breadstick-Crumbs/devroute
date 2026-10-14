# 0011: Task decomposition, delegation plans, and evidence-gated landing

**Status:** proposed (draft for review). Nothing here is implemented; every command and record field below marked *proposed* is new work. The later phases (leases, interface-change notices, the landing queue) are built only if a measurement phase shows they are needed. Covers the Phase 3 roadmap items "Task decomposition" ([#6](https://github.com/Breadstick-Crumbs/susanoo/issues/6)) and "Delegation plans" ([#7](https://github.com/Breadstick-Crumbs/susanoo/issues/7)). Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), [0005](0005-safety-controls-independent-of-profile.md), [0007](0007-hands-off-by-default.md), and [0009](0009-clarifying-questions-under-hands-off.md). If accepted, it amends 0007's list of prompts that stay in every mode (see "Applying a landing"). Schema sketch: [`0011-delegation-plan.schema.json`](0011-delegation-plan.schema.json); example: [`0011-delegation-plan.example.json`](0011-delegation-plan.example.json).

## Context

The DEEP profile already says "delegate independent feature areas to subagents only when they do not share files", but nothing helps Claude do it. Nothing says which parts of a task are independent, which files each part may touch, how the parts depend on each other, or what each part must prove before it is merged. Parallel agents on one repository fail in a few ways:

- **Overlapping edits.** Two agents change the same file, the same lockfile or changelog, or add migrations that conflict in meaning. The merge conflicts, or worse, merges cleanly into something neither agent tested.
- **Interface drift.** One agent changes a function signature or an API shape that another agent builds against.
- **Semantic conflicts.** Agent A renames a function; agent B adds a call to the old name. Each branch passes its checks alone, git merges them without a conflict, and the combined tree is broken. Git sees text, not meaning.
- **Unverified merges.** A coordinator merges a branch because the agent said it was done. Each agent's evidence lives in its own worktree, so the evidence gate in the coordinator's session never sees it.
- **Parallelism that does not pay.** Splitting a task that is really one change costs more in coordination than it saves.

Several tools run agents in parallel on one repository. Claude Code has worktrees (`claude --worktree <name>`), subagents with `isolation: worktree`, and, in the desktop app, separate sessions. Google's experimental [Scion](https://github.com/GoogleCloudPlatform/scion) runs each agent in its own container, with Claude Code among other harnesses. Its CLI includes `scion start <name>`, `scion message <name>`, `list`, `logs`, `attach`, `stop`, `resume`, and `delete`, all through a hub, and a template is a directory with agent configuration, instructions, and skills.

Running agents and letting them talk to each other (Claude Code's agent teams, Scion's hub) are crowded areas that the platforms are absorbing. None of the agent runners named here does **landing**: combining N agent branches into a tree that is still correct, with proof that comes from outside the model. GitHub's merge queue tests combined changes, but only after push, and it knows nothing about agents, plans, or which unit owns what.

Susanoo should not become another agent runner. It is good at the parts they lack: deciding what each part must prove, refusing completion without evidence, and, in the later phases, deciding what may land. That is coordination, not orchestration (ADR 0003): Susanoo never runs agents.

Facts about today's code that constrain the design:

1. **Susanoo cannot decide file ownership from a task description.** Its assessment is keyword rules (ADR 0002). Which files a "billing webhook" unit touches is only known after reading the code. Claude can do that; Susanoo can check the result.
2. **Evidence is per worktree.** In project mode, a worktree has its own `.susanoo/records/` (git-ignored, never merged), provided `.susanoo/` is committed; this repository commits `config.toml` and `.gitignore` there. In global mode, `records_dir()` keys records by the worktree's path, so each worktree gets its own folder under `~/.susanoo/projects/`. Either way, a unit's evidence is somewhere the coordinator's gate does not look.
3. **The stop gate does not run in subagents.** Susanoo registers only `PreToolUse` and `Stop` hooks. A subagent ends with `SubagentStop`, and it shares its parent's session and, most likely, its `CLAUDE_PROJECT_DIR`. So a unit run as a subagent is not held by the evidence gate today.
4. **The gate is a push, not a wall.** A turn end with missing evidence is pushed back up to `max_stop_blocks` times (default 3), then allowed. Automatic routes do not demand required steps whose tool is unavailable.

## Options

1. **Deterministic decomposition only.** Susanoo splits DEEP tasks by detected feature areas and guesses files from keywords. Cheap and testable, but it cannot know file ownership, so its non-overlap guarantee would be fiction.
2. **Claude drafts, Susanoo checks.** Susanoo seeds a skeleton from the assessment. Claude completes it after reading the code: units, owned paths, interfaces, ordering. Susanoo validates it with deterministic rules, computes the schedule, records it, and decides per unit, from the coordinator's own copy of the plan, whether the unit's branch may be merged. Executors are thin adapters that read the same JSON.
3. **Susanoo as orchestrator.** Susanoo starts agents, polls them, and merges. This duplicates Claude Code and Scion and contradicts ADR 0003.
4. **Adopt one executor's format.** Write plans as Scion configuration or as Claude Code task lists. This ties a stable feature to an experimental tool, and the format carries no ownership or evidence rules.

## Decision

Option 2. The same pattern as ADR 0006: model judgment where rules cannot see, inside limits the rules enforce. The merge verdict is the guarantee. Gates inside the units help agents get there, but nothing depends on them.

### The plan

A delegation plan is one JSON document, sketched in [`0011-delegation-plan.schema.json`](0011-delegation-plan.schema.json). It names no executor. It contains:

- **`base`**: the branch and the exact commit the first wave starts from, taken after frozen interfaces and planned dependencies are committed.
- **`units`**: each with an `id`, a self-contained `brief`, the feature area it came from, an estimated `size`, and:
  - **`owns`**: the paths it may change, delete, or create. A path is a file (`src/billing/webhook.py`) or a directory ending in `/` (`src/billing/webhooks/`). No globs in version 1, so overlap is decidable: two entries overlap when they name the same path (ignoring a trailing `/`) or one is a directory that contains the other. Paths are compared after case folding and Unicode NFC normalization, because Windows and macOS file systems usually treat those as the same file.
  - **`reads`**: paths it depends on but must not change (informational).
  - **`provides`** and **`consumes`**: interface ids.
  - **`after`**: units whose branches must be merged before this one starts.
  - **`verification`**: required and recommended step ids, each with a reason.
  - **`floor`**: the minimum profile and the parent route's mandatory controls.
- **`interfaces`**: each with an `id`, a `kind` (`python_api`, `http_api`, `cli`, `schema`, `file_format`, `event`, `other`), exactly one `provider` (a unit id, or `base`), its `consumers`, the `location` where it is defined, a short `contract`, and `frozen`. A frozen interface is committed on the base commit (stub, type, or spec file), so its consumers do not wait for the provider.
- **`dependencies`**: packages a unit needs, added by the coordinator to the base before fan-out, or between waves. Units never edit manifests or lockfiles.
- **`coordinator_owns`**: paths no unit may change; the coordinator changes them after merging. Defaults come from data: lockfiles, `CHANGELOG.md`, package manifests, version files, and `.susanoo/` (so a unit cannot change the configuration that checks it).
- **`serial_paths`**: directories where at most one of the units that can run at the same time may own anything, even different files. Defaults come from data: migration folders (`migrations/`, `alembic/versions/`, `db/migrate/`), because two new migrations can conflict in meaning without touching the same file.
- **`integration`**: verification for the merged result, and the merge order.
- **`recommendation`** and **`schedule`**: computed by Susanoo, never taken from Claude.

The plan stores unit briefs even when `records.store_task_text = false`, because a unit cannot work from a hash. Plans live in `records_dir(root) / "plans" / "<plan_id>.r<revision>.json"`, which both `.gitignore` rules already cover, in project and global mode. A plan never changes in place: a change is a new revision with `supersedes`.

### Producing a plan

1. `/susanoo` routes the task as today. When the route is DEEP and the assessment found at least two feature areas, the brief adds a decomposition step and a skeleton: one candidate unit per feature area, with the parent route's controls in every `floor`. (The `parallelization` dimension alone is not enough: the `cross-cutting` signal raises it without naming areas.) Otherwise the brief says parallel work is unlikely to pay off and offers no plan.
2. Claude reads the code and completes the plan.
3. *Proposed:* `susanoo delegate check <plan.json>` validates it. If it passes, Susanoo stores the revision with the computed schedule and recommendation and appends a `delegations` record. It writes nothing else.

### What `delegate check` enforces

Deterministic rules. A failure lists the reasons; the check never repairs a plan.

- **Shape.** The document matches the schema. The schema is published for other tools. Susanoo checks the same rules with its own small validator in code, because ADR 0001 rules out a `jsonschema` dependency.
- **Safe paths.** Relative POSIX paths inside the repository. Rejected: absolute paths, drive letters, empty, `.` or `..` segments, `.git` in any segment and in any letter case, control characters, backslashes, and glob characters. Paths that resolve outside the root through a symlink are rejected too. Owned paths that do not exist yet are allowed and listed as new files.
- **No overlap between units that can run at the same time.** Two units can run concurrently unless one is a transitive `after` of the other. For every such pair, `owns` must be disjoint, and at most one of them may own anything under each `serial_paths` entry. Units ordered by `after` may own the same path.
- **No unit owns a coordinator path**, or a directory that contains one.
- **The ordering is a DAG.** No cycles, and every `after` names a unit.
- **Interfaces are consistent.** Each has exactly one provider. A consumer of an unfrozen interface comes `after` its provider, and the provider owns the location. A frozen interface's location exists at `base.commit` (`git cat-file -e`) and belongs to nobody's `owns`.
- **The floor matches the parent route.** The route record named by `parent_route_id` must exist. Every unit's `floor.controls` includes every control of that route, and `floor.profile` is at least STANDARD whenever the parent has a control, and otherwise at least FAST. Every step those controls force is in the unit's required steps (ADR 0005: delegation cannot remove a control). Every unit has at least one required step.
- **Recommendation.** `single_agent` when there are fewer than two units, or every unit is `small` and there are at most three. `serial` when no wave has two units. Otherwise `parallel`. The thresholds are data and heuristic, never described as validated. The brief states the costs plainly: one brief and one merge per unit, plus an integration run.

The schedule groups units into waves: wave *n* holds the units whose `after` units are all in earlier waves.

### Inside a unit

Each unit gets a self-contained brief: its task, owned and read-only paths, interfaces with their contracts, required verification, and these rules: commit only inside `owns`; do not change a frozen interface, report instead; do not add dependencies, report instead; record evidence after the last commit.

The plan stays in the coordinator's git-ignored state, so the unit cannot read it. The brief therefore carries what the unit needs to route itself. As its first step the unit runs a *proposed* form of `route` that takes the floor as arguments: `susanoo route --plan <plan_id> --revision <n> --unit <unit_id> --minimum <profile> --require <step> ... "<brief>"`. The unit's route record then carries `plan_id`, `revision`, and `unit_id`, and its required steps include the plan's.

In a separate session (a Claude Code session in the worktree, or a Scion agent), the existing gates then apply in the unit's worktree, if that session's hooks resolve the unit's worktree as the project root. This must be confirmed per executor. For a subagent, they do not apply today (fact 3 above). Running the gates for subagents would need a `SubagentStop` hook and taking the root from the event's working directory. That is separate, proposed work. Until then, the merge verdict below is the only check for subagent units.

**With `hands_off = false`**, a DEEP unit route would wait for a plan approval that only the user can give, once per unit. Instead, a route with `--plan` is not held by the plan-approval gate, and the coordinator side carries the approval: `delegate dispatch` refuses to start units, and `delegate status` refuses `mergeable`, unless the parent route was approved before the plan revision was checked. Susanoo never records an approval the user did not give (ADR 0007).

### The merge verdict

*Proposed:* `susanoo delegate status <plan_id> [--workspace <unit>=<path> ...]`. For each unit it:

1. Locates the workspace from `--workspace` or from the unit's dispatch record. It never searches the disk.
2. Reads the workspace's records with the usual `records_dir()` rule and finds the newest route record with this `plan_id` and `unit_id`. A route for an older revision counts only if the unit's entry is identical in both revisions.
3. **Computes the required steps from the coordinator's own plan, not from the unit's route:** the union of the unit route's required steps, the plan's required steps for the unit, and the steps its floor controls force. It evaluates them strictly: the `auto` exemption for unavailable tools does not apply. Runnable (`ready`) steps need a passing executed run. Other steps need a reported pass, or a reported skip with a note.
4. **Checks freshness.** Counted evidence must have `head` equal to the unit branch's tip and `dirty` false. These are two *proposed* evidence fields: the commit SHA and whether tracked or untracked non-ignored files were changed when the evidence was recorded. Evidence without them never counts.
5. **Checks ownership against the real diff:** `git diff --name-only --no-renames -z <merge-base> <tip>`, where the merge-base is taken between the coordinator's target branch and the unit's tip. `--no-renames` lists both sides of a move, so moving an unowned file into an owned directory is caught. `-z` keeps unusual file names exact. Every path must lie inside `owns`. Because the merge-base moves when a unit rebases onto the newly merged waves, siblings' files do not count against it.
6. **Checks ordering by ancestry:** the merged tip of every `after` unit is an ancestor of the merge-base.
7. **Appends the verdict** to the coordinator's `delegations` records: `mergeable` or `blocked`, the reasons, and the tip SHA it applies to. This record is the only thing it writes.

Output is Markdown, or JSON with `--json`. Text from unit records (notes, output tails) was written by an agent, so status quotes notes, truncates them, and leaves output tails out unless asked. Susanoo does not merge. Until the landing queue (phase 3 below) exists, the coordinator (Claude in the main session, or a person) merges in plan order, and Claude Code's permission settings decide on it, as for any merge. Once a queue exists for the base, branches land through it instead, and the merge gate below denies a direct merge of a queued branch. *Proposed:* `susanoo delegate merged <plan_id> <unit_id>` then checks that the verified tip is an ancestor of the target's HEAD, and records the merge with its provenance (workspace, branch, tip).

**Optional merge gate.** With enforcement installed, the coordinator's PreToolUse hook can deny `git merge` or `git pull` of a unit branch unless the newest verdict for that unit is `mergeable` *for the branch's current tip*. A missing verdict, or one for an older tip, means deny. When a landing queue exists for the base, it also denies a direct `git merge` or `git pull` of any branch in the queue. Like route-before-edit, this is a denial with a reason, never a question.

**Integration.** Unit evidence proves each part on its own base. It does not prove the merged whole. *Proposed:* when a plan is checked, its `integration` steps are added to the parent route's required steps, and the parent route accepts only evidence whose `head` is a commit that contains every unit's merged tip. Without this, checks run before the merges would satisfy the parent route.

**`--reverify`.** Unit records are files an agent can edit. As ADR 0007 says of configuration, this is a guardrail against drift and forgotten checks, not a security boundary. `delegate status --reverify` re-runs a unit's runnable steps itself: in a fresh temporary worktree at the tip, with the coordinator's configuration and registry, not the unit's. This runs agent-written code on the host, outside any container Scion used, so it is opt-in and the brief says so.

### Executors

An executor turns plan units into running agents and tells `delegate status` where each workspace is. Executors are optional. The plan and the verdict work without any, for example with people working in branches.

*Proposed:* `susanoo delegate dispatch <plan_id> --executor <name> [--wave <n>]` renders one brief per unit in the wave, records the wave's base commit, and prints what it would run. With `--execute` it runs those commands once and exits. There is no loop, no polling, and no merge. Commands run as argument lists, never through a shell. Unit ids match `^[a-z0-9][a-z0-9-]{0,39}$`. Agent and branch names are `<plan_id, first 15 characters>-<unit_id, first 24 characters>`, at most 40 characters, which never start with `-`.

#### (a) Claude Code: worktrees and sessions

- **Subagents.** The coordinator starts one subagent per unit in the wave with `isolation: worktree` and the unit brief. Claude Code creates the worktrees under `.claude/worktrees/` and reports their paths. `delegate dispatch --executor claude-code` prints the briefs and branch names, and Claude makes the calls. No gate runs inside these units today. The verdict decides.
- **Separate sessions.** For longer units, `claude --worktree <unit>` in separate terminals, or separate desktop-app sessions, get the same brief. The worktree is a checkout of the same repository, so a committed `.claude/` (skill, subagents, settings hooks) is there. In global mode, the global hooks apply.
- **Messages.** Subagents report to the coordinator when they finish. For separate sessions, Susanoo prints follow-up text (a new interface revision, a blocked verdict); the user or the coordinator sends it.

#### (b) Scion

Facts below are from Scion's docs and source at commit `7e228a6` (2026-10-14), read without running Scion. Scion is pre-1.0 and changes weekly, so they are rechecked before any of this is built.

- **Prerequisites.** Every Scion agent command goes through a hub, so the user runs `scion server start` (a local hub on `127.0.0.1:8080`) and `scion init` in the repository. `scion init` git-ignores `.scion/agents/`. Scion needs git 2.48 or later. After its first agent worktree, the repository's git config requires git 2.48 or later for every tool that touches the repository.
- **Template.** A Scion template is a directory containing:
  - `scion-agent.yaml` (the only required file);
  - optional `agents.md` and `system-prompt.md`;
  - optional `home/` (files copied into the agent's home);
  - optional `skills/<name>/SKILL.md` (mounted into `.claude/skills/` for Claude).

  Every template inherits Scion's `default` template. *Proposed:* `susanoo init --scion-template <dir>` writes `scion-agent.yaml` (Claude harness, unit instructions), `agents.md`, and `skills/susanoo/` into a folder the user names, never changing files it did not create.
  - **The container image is not a template field.** Installing Susanoo in it is a change to Scion's harness configuration or settings, which stays the user's job.
  - **Hooks.** It is not documented whether a template's `home/.claude/settings.json` merges with or replaces the harness's own settings, which register Scion's status hooks. So Susanoo's enforcement hooks come from the repository's committed `.claude/settings.json`, which is present in the agent's worktree, not from the template. A spike confirms that Claude Code picks them up there.
- **Project mode only.** In global mode, a unit's records would go to `~/.susanoo/projects/` inside the container and be lost when it stops. Scion units need the repository's `.susanoo/` committed, so records land in the workspace's `.susanoo/records/` on the host. `delegate check` warns when the executor is Scion and the repository is not in project mode.
- **Start.** For each unit in the wave, `dispatch --executor scion --execute` runs `scion start <agent> --type <template> --task-file <brief file> --no-wait`.
  - The template is always named, because without `--type` Scion uses its default (`gemini`).
  - `--task-file` takes up to 96 KiB, so briefs never go on the command line. That avoids Windows' command-line limit and keeps briefs out of the process list.
  - Scion turns names into slugs. Susanoo's names (`[a-z0-9-]`, no leading `-`) already are slugs, and Susanoo stores the name it expects.
  - `dispatch` refuses an agent name equal to the branch checked out in the main worktree. Scion would otherwise put the agent in the user's own checkout.
- **Messages.** Follow-ups go through `scion message <agent> --body-file <file>`, for example:
  - a new plan revision that changes an interface the unit consumes;
  - a `blocked` verdict with its reasons;
  - "the earlier wave merged; rebase on <commit>".

  `dispatch` and `status` print these commands; with `--execute`, `dispatch` runs them once. "Accepted" from Scion means the hub delivered the message to the agent's input, not that the agent read it.
- **Workspaces and branches.** On the local hub, an agent's worktree is `<repo>/.scion/agents/<agent>/workspace`, and its branch is named like the agent. Susanoo accepts a `--workspace` path for a Scion unit only if it has that shape, exists, and contains a `.git` file. It never lists or reads anything else under `.scion/`: no settings, no templates, nothing that may hold credentials. Scion's shared-workspace modes give no isolation and are refused. `scion delete` deletes the agent's branch, unmerged commits included, unless `--preserve-branch` is passed. So Susanoo never suggests `delete` before the unit's branch is merged or landed.
- **Git inside the container.** Scion creates worktrees with `--relative-paths` and mounts the repository's `.git` at `/repo-root/.git`. The worktree's `.git` file should therefore resolve inside the container, and `head` and `dirty` should be readable there. This comes from reading the mount code; a spike runs `git rev-parse HEAD` and `git status` in a container before anything depends on it.
- **Status.** `delegate status` reads workspaces, not Scion. Only on explicit request does it call `scion list --format json --all`, because `scion list` links an unlinked project to the local hub as a side effect. Scion's agent state is shown next to the verdict, never instead of it:
  - **finished:** phase `stopped`, or `running` with activity `completed`;
  - **failed:** phase `error`, or activity `limits_exceeded`;
  - **needs help:** activity `waiting_for_input`, `blocked`, `stalled`, or `offline`.

  `completed` is reported by the agent itself, so it carries no more weight than the unit's records.
- **Data leaving the machine.** Dispatching to Scion hands unit briefs (task text) to Scion and to the model provider of its harness. Scion also stores the task with the agent record on the hub and writes it to `~/.scion/task.md` in the agent's home. The output of `dispatch --execute` says so.
- **Platform.** Scion publishes Linux and macOS binaries only, runs CI on Linux, and its docs do not mention WSL. On Windows, the only plausible route is WSL 2: the Linux binary, git 2.48 or later on the Linux side, a container runtime reachable from WSL, the repository on the Linux file system, and Susanoo run inside WSL too, so no path translation is needed. This is an inference, not a documented setup, and it needs a spike.

### Discovery

Discovery stays read-only. It adds only:

- whether a `scion` executable is on `PATH` (a name lookup only). Scion gets no version probe, even with `inventory --verify` or `route --verify-tools`. Its version command is `scion version`, not `--version`, and it is local, but any `scion` call reads Scion's own settings, and other commands contact the hub or, like `scion list`, link the project to it;
- whether a `.scion/` directory exists in the project root (existence only).

It never reads `.scion/settings.yaml`, `~/.scion/`, Scion templates, or agent folders. The MCP rule is unchanged: no `env`, `headers`, `args`, or URLs. Executors run only through an explicit `delegate dispatch --execute`.

### Later phases: leases, notices, and a landing queue

These build on the plan format but also work without a plan, for ad-hoc parallel sessions. Formats are sketched in [`0011-coordination-state.example.json`](0011-coordination-state.example.json).

#### Shared state

- **Location.** `<git common dir>/susanoo/`, found with `git rev-parse --path-format=absolute --git-common-dir` (git 2.31 or later; without `--path-format`, the main worktree gets a relative path). Every worktree of a repository sees the same common directory, it is never committed, and it needs no server. Clones, and containers that do not mount it, do not see it (open question 8). Hooks cache the path in their per-session state rather than running git on every call.
- **Writes.** A writer creates `susanoo/lock` exclusively (`O_CREAT | O_EXCL`) with a random nonce, its process id, and the time. It reads the file, writes a temporary file in the same directory, and moves it into place with `os.replace`. Slow work, such as scans and checks, happens outside the lock.
- **Stale locks.** A lock older than 30 seconds is stale. A writer breaks it by renaming it to a unique name and then reading the renamed file. If the nonce is not the one it judged stale, it has taken another writer's live lock: it puts the file back with a rename that fails if a lock already exists (`os.link` on POSIX, `os.rename` on Windows), and it retries from the start. Every holder re-reads its own nonce in the lock file just before `os.replace`, and aborts without writing if the nonce changed.
- **Hooks and readers.** Hooks try the lock once and skip their write on contention; they never wait. On Windows, `os.replace` and `open` can fail briefly while another process holds the file, so readers and writers retry a few times.
- **Notices.** One file each, never rewritten, so they need no lock.
- **Trust.** Any worktree, and so any agent, can write this state, including `refs/susanoo/*`. It is advisory. Anything that moves the base checks the coordinator's own records (below), never `queue.json` or a ref alone.

#### Phase 0: measure first (the kill criterion)

Before phases 1 to 3 are built, Susanoo measures how often they would have mattered, on the user's real parallel work. Phase 0 needs much of phase 3's machinery: temporary worktrees, combined runs, and the evidence `head` field proposed above. What the kill criterion can save is leases, notices, blast radius, and a queue that applies anything. It cannot make phase 0 itself cheap.

- *Proposed:* `susanoo land --measure <branch>...`.
  - It moves no branch.
  - It does write git objects, a temporary worktree, and a `landings` record.
  - It runs the project's checks, so it executes agent-written code on the host, as `--reverify` does. It is an explicit command, and its output says so.
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

#### Phase 1: leases

A lease says which files a worktree is working on, so overlap shows up while agents work, not at merge time.

- **Keyed by worktree, not session.** Subagents share their parent's session id, and probably its project directory (fact 3). The hook maps each edited path to its worktree (`git rev-parse --show-toplevel`, cached per directory) and records it on that worktree's lease. Bash and PowerShell calls carry no file path, so for them the hook uses the event's working directory. The session id is kept for information only.
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

#### Phase 2: interface-change notices

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

#### Phase 3: the evidence-gated landing queue

*Proposed:* `susanoo land`. Branches land one at a time on the local base branch, and each one is checked on the combined tree, before anything is pushed. GitHub's merge queue does a similar job after push. This one is local, and it knows the plan, the leases, and the evidence.

- **Queue.** `<common dir>/susanoo/queue.json` holds entries in order: the plan's merge order when there is a plan, otherwise first come, first served (`susanoo land enqueue <branch>`). `susanoo land drop <branch>` removes an entry.
- **Chain.** Entries that pass build on each other. The queue records the base tip it started from (the chain root), and each entry records the commit it was built `onto`. Applying an entry moves the base onto that entry's landing commit, which keeps the chain intact. The chain breaks in two cases, and then every entry after the break that is not yet applied is invalidated and rebuilt on the next run:
  - the base tip is not the chain root or one of the chain's landing commits, for example after a direct commit to `main` or a `git pull`;
  - an earlier entry is dropped.

  Susanoo never sees a declined ask, so declining a landing means running `land drop <branch>`. Until then, later entries wait. Rebuilding reruns their checks unless the cache below already covers them.
- **One step per run.** `land --next` processes one entry and exits. Each entry moves through stored states: `queued`, `building`, `landed` or `bounced`, then `applied`. A run that is killed (Claude Code's Bash tool stops commands after at most ten minutes) leaves a `building` entry, which the next run resets. That run also removes orphaned temporary worktrees. Nothing runs in the background.
- **Processing an entry.** Like `land --measure` and `--reverify`, this runs agent-written code on the host, outside any container the agent used. `land` is an explicit command, and its output says so.
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

#### Applying a landing

- *Proposed:* `land --apply <branch>` applies exactly one entry, and only when:
  - every earlier entry is already applied;
  - a passing `landings` record in the coordinator's records names that entry's exact landing commit.
- **How the base moves.** In the worktree where the base is checked out, it runs `git merge --ff-only <landing commit>`, and refuses if that worktree has uncommitted changes or the move is not a fast-forward. If the base is not checked out anywhere, it uses `git update-ref` with the old value as a check. It then releases the lease and records the merge, as `delegate merged` does.
- **Always asks.** The PreToolUse hook asks the user before `land --apply`, in every mode, like `susanoo approve` in ADR 0007, so each branch lands with the user's say-so. It also asks before git commands that move the base to a commit under `refs/susanoo/landed/` (`git merge`, `git reset`, or `git update-ref` naming such a ref or its commit). Accepting this ADR adds these to ADR 0007's list of prompts that stay in every mode.
- **No push.** `land` never pushes. After landing, the user pushes, and the remote CI or merge queue runs as usual.
- **Not in version 1:** batching several branches and bisecting failures, as large merge queues do.

#### Standalone product?

Leases, notices, and the queue do not depend on Claude Code. They could become a separate, harness-agnostic tool. The recommendation is to build them inside Susanoo first, under the same stdlib-only and read-only rules, and to extract them only if the phase 0 measurement and real use justify it.

### What Susanoo will not do

- Run a long-lived process, poll agents, or push. Each command does one thing and exits (ADR 0003).
- Move the base branch itself without asking the user. Plain git can still move it outside the patterns the hook recognizes; as in ADR 0007, this is a guardrail, not a security boundary.
- Claim a plan is good, or that parallel work is faster. The check proves consistency, not quality. Whether delegation pays off is a Phase 4 measurement, reported with sample sizes.
- Add runtime dependencies. Executors are external programs called with argument lists (ADR 0001).
- Read or pass on credentials. Unit briefs contain no environment values.

## Consequences

- One plan format serves Claude Code subagents and sessions, Scion, and people working in branches. Dropping Scion later, or adding another executor, changes no plan.
- Non-overlap holds at merge time, because the verdict checks the actual diff against the coordinator's copy of the plan. A unit that strayed is blocked before it is merged, whatever its own gate did.
- Coordinator paths, dependencies, and migration folders become serial work. This is slower but avoids the most common conflicts.
- New proposed record data: `head` and `dirty` on evidence; `plan_id`, `revision`, and `unit_id` on routes; a `delegations` record kind. Records without them never count toward a verdict.
- Decomposition quality depends on Claude and is not tested by Susanoo. Tests cover the validator, schedule, verdict rules, and executor command rendering. A labeled suite of fixed plans with expected waves and conflicts checks the rules, not Claude.
- A frozen interface that turns out wrong costs a new plan revision and a message to every consumer.
- The later phases add Susanoo state inside `.git` (the common directory). It is local, never pushed, and removed with the clone. `susanoo doctor` should show it and `susanoo lease prune` should clean it.
- The landing queue runs the project's checks at least once per branch on the combined tree, plus setup in a fresh worktree, plus one rerun and one solo run per failure. That is the real cost, and the reason for the cache and for measuring first.
- If phase 0 finds no semantic breaks and fewer than three textual conflicts between agent branches, phases 1 to 3 shrink to a dry-run report. The kill criterion is part of this decision, not an afterthought.

## Open questions

1. Scion, still to confirm by running it (the rest was answered from its docs and source; see the Scion section):
   - whether `git rev-parse HEAD` and `git status` work inside an agent container, under both Docker and Podman;
   - whether Claude Code in the container picks up the repository's `.claude/settings.json` hooks;
   - whether a template's `home/.claude/settings.json` merges with the harness's or replaces it.
2. Scion on Windows: whether the WSL 2 route works end to end. Scion does not document it.
3. Separate Claude Code sessions in a worktree: confirm that their hooks resolve the worktree, not the main checkout, as the project root.
4. Whether `owns` should allow globs later. They make overlap checks approximate, so version 1 leaves them out.
5. Whether evidence should also travel as git notes on the unit's tip commit, for clone-based and remote executors, at the cost of a second evidence path.
6. Whether `--reverify` should be the default for units under a security or data control, where self-reported evidence matters most.
7. Passing every parent control to every unit is conservative. In the example, the settings-page unit inherits `migration_check` although it changes no schema: it records a skip when the step is manual, but must run it when runnable. Dropping a control for a unit would need a deterministic rule, and none exists yet.
8. Leases and notices in containers and clones. Scion mounts the repository's `.git` into its containers, so local Scion agents should see the common directory. Under Docker some `.git` paths are mounted read-only or narrowed, which needs checking. Clone-mode workspaces (Scion's remote hubs) have their own `.git`, and would need an exported lease file or the git-notes path from question 5.
9. Whether Claude Code's agent-teams mailbox can be written from outside a team (from a hook or the CLI); if not, that adapter is dropped.
10. Whether the Python blast-radius scan should also follow `from package import name` through `__init__.py` re-exports, which is more accurate and slower.
11. Whether leases, notices, and landing should later become a standalone, harness-agnostic tool. The current answer is no, until measurement justifies it.
12. `[land] setup`: how to build a correct environment in a fresh temporary worktree for each ecosystem (Python editable installs, `node_modules`, compiled languages) without network surprises or long setup times. This needs a spike before phase 0, because phase 0 depends on it.
13. Whether `land` should also ask Claude Code's agent teams or Scion to pause the affected agents while their branch is bounced. The current answer is no: the bounce is a notice, and agents keep working.
