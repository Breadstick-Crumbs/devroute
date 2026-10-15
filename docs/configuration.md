# Configuration

Susanoo works with no configuration. Everything below is optional.

## Files and precedence

| File | Purpose |
|---|---|
| `src/susanoo/data/defaults.toml` (packaged) | All default heuristics: signals, weights, thresholds, controls, verification rules |
| `~/.susanoo/config.toml` | Your overrides for every project (global mode), merged after the defaults |
| `.susanoo/config.toml` | Project overrides, merged after your user overrides |
| `--config <file>` | Extra overrides for one command (merged last) |
| `.susanoo/tools.toml` | Add or replace tool registry entries (`[[tools]]`, matched by `id`; `enabled = false` removes one) |
| `.susanoo/profiles/<fast\|standard\|deep>.toml` | Override fields of a workflow profile |

`SUSANOO_HOME` moves `~/.susanoo` (global install manifest, user configuration, and global-mode records), and `CLAUDE_CONFIG_DIR` moves the Claude Code configuration directory that `susanoo init --global` writes to.

**Merge rules:** tables merge recursively. `[[signals]]`, `[[controls]]`, and `[[verification]]` merge **by `id`**, so you can change one entry without copying the catalog. An entry with `enabled = false` removes the default with that id. Other arrays are replaced. `susanoo doctor` validates the merged result and reports the exact offending key.

## Common changes

### Make routing stricter for a sensitive codebase

```toml
schema_version = 1

[risk]
thresholds = { moderate = 0.25, high = 0.50, critical = 0.80 }

[routing]
critical_risk_floor = "deep"   # overrides can never go below DEEP when risk is critical
```

### Teach Susanoo your domain

```toml
[[signals]]
id = "ledger"
label = "ledger / accounting code"
area = "accounting"
patterns = ["ledger", "reconcil\\w*", "journal entr\\w*"]
effects = { data_integrity = 3, security_privacy = 2 }
requires = ["integration_testing"]
```

Patterns are case-insensitive regular expressions matched on word boundaries. In TOML basic strings (`"..."`), escape backslashes (`\\w`), or use literal strings (`'\w'`).

### Change what a signal does, or turn it off

```toml
[[signals]]
id = "notifications"
effects = { external_dependencies = 1, affected_components = 1 }

[[signals]]
id = "performance"
enabled = false
```

### Require a check in every profile

```toml
[[verification]]
id = "type_check"
levels = { fast = "required", standard = "required", deep = "required" }
```

### Register an in-house tool

`.susanoo/tools.toml`:

```toml
[[tools]]
id = "acme-contract-tests"
name = "ACME contract tests"
kind = "script"
capabilities = ["integration_testing"]
make_targets = ["contract-test"]
run = { integration_testing = ["make", "contract-test"] }
reliability = 3
context_cost = 1
setup_cost = 1
security_exposure = 1
```

To declare a browser Susanoo cannot detect (Claude in Chrome, for example) for the UI visual check, add an entry without a `run` command, so the check stays one that Claude performs and reports:

```toml
[[tools]]
id = "claude-in-chrome"
name = "Claude in Chrome"
kind = "native"
capabilities = ["visual_check"]
env = ["CLAUDECODE"]   # set in every Claude Code session
```

Detection keys are those listed at the top of [`tools.toml`](../src/susanoo/data/tools.toml); any one that matches counts, except `requires_files` and `requires_python_packages`, which must also match (screenshot baselines for visual regression; pytest-xdist for parallel pytest). A required Python package counts when it is installed in the project's own environment; when it is declared and installed beside an executable found on PATH; or, when the checks run in an environment outside the project (Poetry, Pipenv, uv) or no executable is found, when it is declared. An environment that cannot be told apart (a pyenv shim, a copied launcher, several Python versions sharing one prefix) counts as not having it. `supersedes` names tools an entry is a better form of: where both are usable it ranks first, and where it is not detected it is never suggested. Disabling the superseded tool (`enabled = false`) disables the entry too, and a project's own entry for the superseded tool wins over a packaged one that supersedes it. The packaged `pytest-xdist` entry uses both, so `susanoo verify --execute` runs `python -m pytest -n auto -q` where pytest-xdist is there and plain `python -m pytest -q` elsewhere; `enabled = false` for `pytest-xdist` turns parallel runs off for a suite that cannot run in parallel, and a project's own `pytest` entry keeps its command.

A tool whose command depends on the project's own configuration lists `[[tools.configured_run]]` tables after its other keys: `when` names `file:section[:key]` sources in the tool's lookup order (`pyproject.toml:tool.mypy:files`), and `run` replaces those capabilities' commands when the first of those files the tool would read sets that key; the first matching table wins. A tool-specific file (`mypy.ini`) decides once it exists and parses; a shared one (`pyproject.toml`, `setup.cfg`, `tox.ini`) only once it has the section. Susanoo only checks that the key is set and never puts its value into a command; the file must be in the project root.

Project tool entries are repository-controlled. Susanoo ignores their `version_args`, so `--verify` never runs them. Their `run` commands do execute under `susanoo verify --execute`, just like the project's own test scripts.

### Keep task text out of records

```toml
[records]
store_task_text = false   # only a SHA-256 of the task is stored
```

### Capture tokens and duration from Claude Code

```toml
[records]
capture_usage = true   # off by default
```

When an outcome is recorded (`susanoo outcome`, or Susanoo closing a route a new task replaced), Susanoo reads the route's Claude Code session transcript (`<CLAUDE_CONFIG_DIR or ~/.claude>/projects/*/<session>.jsonl` and its subagents' transcripts) and fills the outcome's fields that were not given. Only assistant messages written between the route's creation and the outcome count, and work after the session's next route belongs to that route.

- `token_counts`: fresh input, cache writes, cache reads, and output tokens, kept apart (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`), so prompt-cache use stays visible. Left out when you pass `--tokens`, so it never disagrees with `tokens`. Cache reads cost far less than fresh input, so weight them separately in any cost estimate.
- `tokens`: the sum of the four.
- `duration_minutes`: from the route's creation to its last assistant message.
- `tool_calls`: calls per tool name (`Read`, `Grep`, `mcp__<server>__<tool>`), counted from tool-use blocks without reading their inputs or results.
- `cost_usd`: only when the transcript itself records a cost (older Claude Code versions did). Susanoo does not estimate prices.
- `time_minutes`: where `duration_minutes` went, from the session's own transcript. Each gap between two transcript lines counts toward what ended it:
  - `model`: Claude writing a message (including thinking and API time);
  - `tools`: a tool running, including subagents and the auto-mode permission check;
  - `approvals`: a tool call that waited for you to answer a permission prompt, including its run time afterwards;
  - `waiting`: the turn had ended and the session waited for the next message, from you or from the app (another session's report, a task notification).

  The four parts add up to `duration_minutes`, give or take rounding. Time after you interrupt Claude counts as waiting, because the transcript marks an interruption only in message text, which is never read.
- `tool_minutes`: the `tools` and `approvals` minutes per tool name, most first (`{"Bash": 34.7, "Agent": 9.8}`). Which command a `Bash` call ran is never read.
- `user_prompts`: prompts you typed after Claude's first message in the route. They are often corrections or redirects, but also answers to Claude's questions.
- `approvals`: permission prompts you answered (approved or rejected).

`captured` lists the fields that came from the transcript. Only usage numbers, timestamps, message and line ids, tool names and tool-use ids, line and block types, the `isMeta` and `isCompactSummary` flags, whether a prompt was typed by you (`origin.kind`), and who allowed a tool call (`permissionDecision.source`) are read, never message text or tool inputs and results. The transcript format is not a documented interface: when it is missing or changes, nothing is captured and the outcome is recorded as before. Routes made outside a Claude Code session have no session id and are never captured; work after `/clear` or in a resumed session with a new session id is not counted. Reading stops, and nothing is captured, after about 2 seconds or 64 MB, so a route left open for a very long session may get no capture; this keeps the prompt hook fast. Decoding the session's user messages for the time split has its own half-second budget on top: past it, the time fields are left out and the rest is captured as before.

## Reference

### `[assessment]`
- `min_words`: descriptions shorter than this are low-confidence.
- `stacking_level`: two or more independent signals at or above this level raise a dimension one extra step.
- `baseline`: starting levels per dimension (default: `implementation_complexity = 1`).

### `[assessment.negation]`
An instruction to leave a part of the code base alone is ignored when signals are matched, and shown as "Ignored as out of scope" in the assessment.
- `patterns`: regexes for the instruction, such as `without (?:touching|changing|...) {target}`. `{target}` expands to an optional determiner, up to `filler` plain words (default 3; never a clause word such as "and" or "we"), and one of `locations`; `{location}` is a location alone. The ignored span ends at the location, so anything after it still counts.
- `locations`: code-location nouns (module, file, page, schema, database, ...). Without a location nothing is ignored, so preservation requirements such as "without breaking payments" keep their risk.
- `keep_signals`: signal ids that are never ignored, even inside an instruction (default: destructive data operations and production work).
- `requires_signals`: one of these signals must still match outside the ignored text (default: `trivial-edit`, a signal with no effects that matches typo, wording, rename, docstring, formatting, ...). `blocking_signals` must not match (default: new functionality). `clause`: after removing the ignored text and its connecting comma or "and"/"but", the rest must not match this pattern or contain a second sentence. If any of these fails, nothing is ignored. These are keyword checks, which is why they are strict.
- Line breaks and bullets are treated as sentence ends, and a target never runs past one.

Keep this narrow. Ignoring text can only lower risk.

### `[assessment.presentational]`
A visual redesign ("Redesign the pricing page layout for mobile") does not count as an architectural change ([ADR 0008](decisions/0008-ui-ux-workflow.md)). The packaged configuration has one rule, for `architecture-change`, and deliberately none for `payments`: a heuristic never removes payment controls. The brief lists what was ignored as "Ignored as a purely visual change". The task is read word by word against a closed vocabulary and a fixed structure (see the ADR):
- `max_words` (default 20) and `reject_words` ("and", "or", "then", ...): longer tasks, tasks with one of these words, and tasks with a comma, semicolon, colon, question mark, line break, or sentence end inside are never presentational. A leading "please" and a trailing period or exclamation mark are ignored.
- `determiners`, `ui_nouns`, `appearance`, `values` (allowed after "to": colors, sizes), `platform`, `subjects` (payment-page words, allowed only in the object before a UI noun), `page_names` (the only other words allowed in the object, at most one), `phrases` (multi-word entries read as one word): lowercase words. Hex colors count as appearance words.
- `keep`: regexes that keep every signal when they match anywhere, also with hyphens read as spaces (payment logic, providers, data, state, flows, card or text fields, APIs, authentication, ...).
- `rules`: each has `signals` (ids to ignore), `verbs` (the first word of the task), and optionally `require_appearance = true` (an appearance word must appear; platform words do not count). A signal is only ignored if every phrase it matched is a rule verb or a `subjects` word. `rules = []` turns it off.
- Unknown keys are an error. Lists replace the defaults rather than adding to them, so a project that sets `keep` must repeat every default entry it wants to keep.

Keep this narrow. Ignoring a signal can only lower risk. A project can add rules, including for other signals, but the packaged data deliberately never ignores `payments`.

A signal can also limit its `caps` with `caps_if_signals` (caps apply only when one of these signals matched), next to `caps_unless_signals` and `caps_only_if`.

### `[complexity]`, `[risk]`, `[uncertainty]`
- `weights` (complexity): relative weights of the complexity dimensions.
- `dimensions`, `max_weight`, `critical_severe_dimensions` (risk): see [architecture](architecture/overview.md).
- `thresholds`: increasing values between 0 and 1.
- `low_confidence_penalty` (uncertainty): added when assessment confidence is low.

### `[[signals]]`
`id`, `label`, `patterns` (required), `effects` (dimension → 0-3), `caps` (upper bounds, ignored if a `kind = "scope"` signal matched), `caps_only_if` (regexes; the caps apply only when the whole task matches one of them: the `tests-only` signal uses it so only a short, plain test request is capped, and "add tests and fix the bug" keeps its full risk), `caps_unless_signals` (signal ids that disable the caps when they match), `area` (feature area name), `kind`, `requires` / `recommends` (capability ids).

### `[[controls]]`
`id`, `when = { dimension, min }`, `description`, `checkpoint`, `verification` (step ids forced to required).

### `[[verification]]`
`id`, `title`, `capability`, `levels = { fast|standard|deep = "required"|"recommended" }`, `escalate = [{ dimension, min, level } | { signal, level }]`, `signals` (only include when one matched), `instructions` (how to perform and record the check, shown in the brief), `only_with_tool = true` (leave the step out when no usable tool provides its capability, instead of showing a gap; a required step is never left out), `evidence = "reported"` (the check is always performed and reported by the agent or a person, even if its tool has a run command), `ui_domains = [...]` (only for UI tasks in those domains; a safety control that names the step still includes it, and an empty list means no restriction). `{command}` in `instructions` becomes the command that runs Susanoo.

UI tasks (the `ui` signal) get `visual_check` (recommended in FAST, required in STANDARD and DEEP, always reported evidence), `accessibility_scan` (recommended in STANDARD, required in DEEP), and `visual_regression` (recommended, only when screenshot baselines exist). See [ADR 0008](decisions/0008-ui-ux-workflow.md).

### Dimensions
`implementation_complexity`, `architectural_complexity`, `affected_components`, `technical_uncertainty`, `external_dependencies`, `security_privacy`, `data_integrity`, `irreversibility`, `testing_difficulty`, `parallelization`.

### Capabilities
Run `susanoo research --list`.

### `[enforcement]`
Settings for opt-in hook enforcement: `hands_off` (default `true`: Susanoo asks nothing and blocks no edit in any permission mode, only the evidence gate applies, and briefs report checkpoints instead of stopping at them; set `false` for the approval and confirmation gates), `approval_profiles`, `evidence_profiles`, `max_stop_blocks`, `edit_tools`, `shell_write_patterns`, `irreversible_commands`, and `autonomous_modes` (Claude Code permission modes in which Susanoo adds no approvals or questions; default: acceptEdits, auto, dontAsk, bypassPermissions; set it to `[]` to keep every gate in every mode). See [enforcement.md](enforcement.md).

### `[auto]`
Automatic routing of prompts (`susanoo init --global`, or `--auto` with the settings scope). A prompt is a task when, after leading `polite` phrases ("please", "can you", "ok now", ...), it starts with one of `task_verbs` and has at least `min_words` words; it is a question when it starts with one of `question_starts`; anything else is ambiguous. Questions and ambiguous prompts are not routed by the hook; they get a short note, and Claude routes them itself if they need code changes (never lower than the keyword result for the prompt). With `route_before_edit = true` (the default), the first edit of a project file for such a prompt, with no route open, is denied once so Claude routes first; a retry goes through. `route_before_edit_exempt` lists project paths it leaves alone (shell-style patterns; by default agent memory such as `CLAUDE.md` and `AGENTS.md`, and `.claude/` folders). `skip_prefix` (default `?`) marks a prompt as never routed, and a task containing an `autonomy` phrase ("work autonomously", "don't ask") runs hands-off. All other entries are regular expressions.

### `[ui]`
`domain` (default `""`): the UI domain for UI tasks ([ADR 0008](decisions/0008-ui-ux-workflow.md)). Empty picks one from the task's words, then from the project's dependencies and files; `website`, `ecommerce`, `dashboard`, `mobile`, or `component-library` fixes it; `"none"` shows only the generic UI baseline. Set it in the project's `.susanoo/config.toml` or in `~/.susanoo/config.toml`. The domains, their task words, detection hints, and checklists are in [`ui_domains.toml`](../src/susanoo/data/ui_domains.toml); a `[[verification]]` rule with `ui_domains = [...]` applies only to those domains (the performance budget is `website` only). The same file's `[design_context]` lists where design documents, tokens, and Storybook are looked for and what UI briefs say about them.

### Architecture context
Where STANDARD and DEEP briefs look for architecture documents, decision records, architecture sections of agent instructions, and boundary rules, and what they say about them, is packaged data in [`architecture.toml`](../src/susanoo/data/architecture.toml) ([ADR 0011](decisions/0011-architecture-context.md)). Boundary tools Susanoo can run (import-linter, dependency-cruiser, eslint-plugin-boundaries) are registry entries with the `architecture_boundaries` capability; the `architecture_check` step that runs them is recommended in STANDARD and DEEP and left out when no such tool is configured. Register another boundary tool with a `.susanoo/tools.toml` entry that lists `architecture_boundaries` and a `run` command for it.

### `[land]`
What `susanoo land` runs on the combined tree of the branches it lands ([ADR 0014](decisions/0014-susanoo-land.md)), read from the base branch's configuration: `use_verification` (default `true`: the ready, required steps of the DEEP verification plan), `require_review` (default `true`: `--merge` refuses a candidate without a passing code-review recorded for the tip it tests with `susanoo review record`; without `--merge` a missing review is only reported; read only from the defaults and the base's project file, never from the user configuration; [ADR 0015](decisions/0015-lead-session-and-board.md)), `setup` (commands run in the scratch tree after every checkout), `env` (`{tree}` is the scratch tree's path), `[[land.checks]]` (replace a verification step's command by `id`, or add a check; `enabled = false` drops one), `[[land.compare]]` (a JSON-printing command compared between the base and the combined tree when the candidates change one of its `paths`; `no_drop` and `no_rise` name the numbers), and `[[land.keep_both]]` (append-only places where a conflict is resolved by keeping both sides: `path`, optional `section`, `lines`, and `sort`). See [land.md](land.md).

### `[selection]`
`code_navigation_size_adjustment`: how strongly repository size (compared with `context.large_repo_files`) shifts code-navigation choices between built-in search and indexed tools such as code-graph MCP servers. `task_keyword_bonus` (default 3): added to a tool whose `task_keywords` appear in the task ("postgres" picks psql over sqlite3); smaller than the gap between an installed and a missing tool. `default_package_manager` (default `npm`; also `pnpm`, `yarn`, `bun`): the JavaScript package manager for install hints when the project names none (package.json `packageManager`) and has no lockfile. Package-script commands always use the manager discovery checked for (npm without a lockfile), so a ready check never runs a binary that was not found.

### `[verify]`
`timeout_seconds` (default 900, at most 86400): how long each command `susanoo verify --execute` runs may take before it is stopped and recorded as a timeout; `--timeout` overrides it for one run. The timeout evidence says how to raise it. Raise it for a long suite, or when several suites run at once on one machine: load alone tripled one full run of Susanoo's own suite (220 s to 664 s).

## Checking the effect of a change

```bash
susanoo doctor                 # validates configuration
susanoo eval                   # routing on the bundled suite
susanoo eval --suite my.toml   # your own labeled tasks
susanoo assess --json "..."    # inspect individual scores and reasons
```
