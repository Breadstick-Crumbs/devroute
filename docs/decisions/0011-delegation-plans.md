# 0011: Task decomposition and delegation plans as tool-neutral JSON

**Status:** proposed (draft for review). Nothing here is implemented; every command and record field below marked *proposed* is new work. Covers the Phase 3 roadmap items "Task decomposition" ([#6](https://github.com/Breadstick-Crumbs/susanoo/issues/6)) and "Delegation plans" ([#7](https://github.com/Breadstick-Crumbs/susanoo/issues/7)). Works within [0001](0001-zero-runtime-dependencies.md), [0003](0003-skill-first-claude-code-integration.md), [0004](0004-local-jsonl-records.md), [0005](0005-safety-controls-independent-of-profile.md), and [0007](0007-hands-off-by-default.md). Schema sketch: [`0011-delegation-plan.schema.json`](0011-delegation-plan.schema.json); example: [`0011-delegation-plan.example.json`](0011-delegation-plan.example.json).

## Context

The DEEP profile already says "delegate independent feature areas to subagents only when they do not share files", but nothing helps Claude do it. Nothing says which parts of a task are independent, which files each part may touch, how the parts depend on each other, or what each part must prove before it is merged. Parallel agents on one repository fail in a few ways:

- **Overlapping edits.** Two agents change the same file, the same lockfile or changelog, or add migrations that conflict in meaning. The merge conflicts, or worse, merges cleanly into something neither agent tested.
- **Interface drift.** One agent changes a function signature or an API shape that another agent builds against.
- **Unverified merges.** A coordinator merges a branch because the agent said it was done. Each agent's evidence lives in its own worktree, so the evidence gate in the coordinator's session never sees it.
- **Parallelism that does not pay.** Splitting a task that is really one change costs more in coordination than it saves.

Several tools run agents in parallel on one repository. Claude Code has worktrees (`claude --worktree <name>`), subagents with `isolation: worktree`, and, in the desktop app, separate sessions. Google's experimental [Scion](https://github.com/GoogleCloudPlatform/scion) runs each agent in its own container, with Claude Code among other harnesses. According to its README, its CLI has `scion start <name> "<task>"`, `scion message <name> "..."`, `list`, `logs`, `attach`, `stop`, `resume`, and `delete`, and a template is a system prompt plus skills.

Susanoo should not become another one of these. It is good at the parts they lack: deciding what each part must prove, and refusing completion without evidence.

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
- **`coordinator_owns`**: paths no unit may change; the coordinator changes them after merging. Defaults come from data: lockfiles, `CHANGELOG.md`, package manifests, version files.
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

Output is Markdown, or JSON with `--json`. Text from unit records (notes, output tails) was written by an agent, so status quotes notes, truncates them, and leaves output tails out unless asked. Susanoo does not merge. The coordinator (Claude in the main session, or a person) merges in plan order. *Proposed:* `susanoo delegate merged <plan_id> <unit_id>` then checks that the verified tip is an ancestor of the target's HEAD, and records the merge with its provenance (workspace, branch, tip).

**Optional merge gate.** With enforcement installed, the coordinator's PreToolUse hook can deny `git merge` or `git pull` of a unit branch unless the newest verdict for that unit is `mergeable` *for the branch's current tip*. A missing verdict, or one for an older tip, means deny. Like route-before-edit, this is a denial with a reason, never a question.

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

- **Template.** The user creates a Scion template for Susanoo units, for example `susanoo-unit`: the Claude Code harness, the `/susanoo` skill, Claude Code settings with Susanoo's enforcement hooks, and an image with Susanoo installed. *Proposed:* `susanoo init --scion-template <dir>` writes the Susanoo parts into a folder the user names, never changing files it did not create. Scion's template format must be confirmed first.
- **Project mode only.** In global mode, a unit's records would go to `~/.susanoo/projects/` inside the container and be lost when it stops. Scion units need the repository's `.susanoo/` committed, so records land in the workspace's `.susanoo/records/`. `delegate check` warns when the executor is Scion and the repository is not in project mode.
- **Start.** For each unit in the wave, `dispatch --executor scion --execute` runs `scion start <agent> "<unit brief>"` with the template selected, passing `--` before positional arguments if Scion supports it. Long briefs are a problem: Windows limits a command line to about 32,000 characters, and arguments are visible in the process list. Whether Scion can take a task from a file must be confirmed.
- **Messages.** Follow-ups go through `scion message <agent> "<text>"`: a new plan revision that changes an interface the unit consumes, a `blocked` verdict with its reasons, or "the earlier wave merged; rebase on <commit>". `dispatch` and `status` print these commands; with `--execute`, `dispatch` runs them once.
- **Workspaces.** Susanoo takes each workspace path from `--workspace`, or from a fixed layout once Scion's is confirmed. It never lists or reads anything else under `.scion/`: no settings, no templates, nothing that may hold credentials. A workspace that shares one directory between agents is refused, because it has no isolation.
- **Data leaving the machine.** Dispatching to Scion hands unit briefs (task text) to Scion and to the model provider of its harness. The brief to `dispatch --execute` says so.
- **Platform.** Scion needs a container runtime (Docker or Podman, among others its README lists). Its Windows support is unknown. The likely setup on Windows is WSL 2 with the repository on the Linux file system, and this needs a spike before anything is promised.

### Discovery

Discovery stays read-only. It adds only:

- whether a `scion` executable is on `PATH` (a name lookup; Scion gets no version probe, even with `inventory --verify` or `route --verify-tools`, because its commands may contact its server);
- whether a `.scion/` directory exists in the project root (existence only).

It never reads `.scion/settings.yaml`, `~/.scion/`, Scion templates, or agent folders. The MCP rule is unchanged: no `env`, `headers`, `args`, or URLs. Executors run only through an explicit `delegate dispatch --execute`.

### What Susanoo will not do

- Run a long-lived process, poll agents, or merge branches. Each command does one thing and exits (ADR 0003).
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

## Open questions

1. Scion: the template format and how skills attach; how `scion start` selects a template and whether it accepts `--` or a task file; where git-worktree workspaces live on the host and how branches are named; whether `scion list` has machine-readable output. Inside a container, a worktree's `.git` file points to an absolute host path, so `head` and `dirty` may not be readable there. Without them, no Scion unit's evidence counts.
2. Scion on Windows: whether WSL 2 is enough, and how path translation affects `--workspace`.
3. Separate Claude Code sessions in a worktree: confirm that their hooks resolve the worktree, not the main checkout, as the project root.
4. Whether `owns` should allow globs later. They make overlap checks approximate, so version 1 leaves them out.
5. Whether evidence should also travel as git notes on the unit's tip commit, for clone-based and remote executors, at the cost of a second evidence path.
6. Whether `--reverify` should be the default for units under a security or data control, where self-reported evidence matters most.
7. Passing every parent control to every unit is conservative. In the example, the settings-page unit inherits `migration_check` although it changes no schema: it records a skip when the step is manual, but must run it when runnable. Dropping a control for a unit would need a deterministic rule, and none exists yet.
