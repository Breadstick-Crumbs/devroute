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

Project tool entries are repository-controlled. Susanoo ignores their `version_args`, so `--verify` never runs them. Their `run` commands do execute under `susanoo verify --execute`, just like the project's own test scripts.

### Keep task text out of records

```toml
[records]
store_task_text = false   # only a SHA-256 of the task is stored
```

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
`id`, `title`, `capability`, `levels = { fast|standard|deep = "required"|"recommended" }`, `escalate = [{ dimension, min, level } | { signal, level }]`, `signals` (only include when one matched).

### Dimensions
`implementation_complexity`, `architectural_complexity`, `affected_components`, `technical_uncertainty`, `external_dependencies`, `security_privacy`, `data_integrity`, `irreversibility`, `testing_difficulty`, `parallelization`.

### Capabilities
Run `susanoo research --list`.

### `[enforcement]`
Settings for opt-in hook enforcement: `hands_off` (default `true`: Susanoo asks nothing and blocks no edit in any permission mode, only the evidence gate applies, and briefs report checkpoints instead of stopping at them; set `false` for the approval and confirmation gates), `approval_profiles`, `evidence_profiles`, `max_stop_blocks`, `edit_tools`, `shell_write_patterns`, `irreversible_commands`, and `autonomous_modes` (Claude Code permission modes in which Susanoo adds no approvals or questions; default: acceptEdits, auto, dontAsk, bypassPermissions; set it to `[]` to keep every gate in every mode). See [enforcement.md](enforcement.md).

### `[auto]`
Automatic routing of prompts (`susanoo init --global`, or `--auto` with the settings scope). A prompt is a task when, after leading `polite` phrases ("please", "can you", "ok now", ...), it starts with one of `task_verbs` and has at least `min_words` words; it is a question when it starts with one of `question_starts`; anything else is ambiguous. Questions and ambiguous prompts are not routed by the hook; they get a short note, and Claude routes them itself if they need code changes (never lower than the keyword result for the prompt). `skip_prefix` (default `?`) marks a prompt as never routed, and a task containing an `autonomy` phrase ("work autonomously", "don't ask") runs hands-off. All entries are regular expressions.

### `[selection]`
`code_navigation_size_adjustment`: how strongly repository size (compared with `context.large_repo_files`) shifts code-navigation choices between built-in search and indexed tools such as code-graph MCP servers. `task_keyword_bonus` (default 3): added to a tool whose `task_keywords` appear in the task ("postgres" picks psql over sqlite3); smaller than the gap between an installed and a missing tool. `default_package_manager` (default `npm`; also `pnpm`, `yarn`, `bun`): the JavaScript package manager for install hints and package-script commands when the project names none (package.json `packageManager`) and has no lockfile.

## Checking the effect of a change

```bash
susanoo doctor                 # validates configuration
susanoo eval                   # routing on the bundled suite
susanoo eval --suite my.toml   # your own labeled tasks
susanoo assess --json "..."    # inspect individual scores and reasons
```
