# Configuration

Susanoo works with no configuration. Everything below is optional.

## Files and precedence

| File | Purpose |
|---|---|
| `src/susanoo/data/defaults.toml` (packaged) | All default heuristics: signals, weights, thresholds, controls, verification rules |
| `.susanoo/config.toml` | Project overrides, deep-merged over the defaults |
| `--config <file>` | Extra overrides for one command (merged last) |
| `.susanoo/tools.toml` | Add or replace tool registry entries (`[[tools]]`, matched by `id`; `enabled = false` removes one) |
| `.susanoo/profiles/<fast\|standard\|deep>.toml` | Override fields of a workflow profile |

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

### `[complexity]`, `[risk]`, `[uncertainty]`
- `weights` (complexity): relative weights of the complexity dimensions.
- `dimensions`, `max_weight`, `critical_severe_dimensions` (risk): see [architecture](architecture/overview.md).
- `thresholds`: increasing values between 0 and 1.
- `low_confidence_penalty` (uncertainty): added when assessment confidence is low.

### `[[signals]]`
`id`, `label`, `patterns` (required), `effects` (dimension → 0-3), `caps` (upper bounds, ignored if a `kind = "scope"` signal matched), `area` (feature area name), `kind`, `requires` / `recommends` (capability ids).

### `[[controls]]`
`id`, `when = { dimension, min }`, `description`, `checkpoint`, `verification` (step ids forced to required).

### `[[verification]]`
`id`, `title`, `capability`, `levels = { fast|standard|deep = "required"|"recommended" }`, `escalate = [{ dimension, min, level }]`, `signals` (only include when one matched).

### Dimensions
`implementation_complexity`, `architectural_complexity`, `affected_components`, `technical_uncertainty`, `external_dependencies`, `security_privacy`, `data_integrity`, `irreversibility`, `testing_difficulty`, `parallelization`.

### Capabilities
Run `susanoo research --list`.

### `[enforcement]`
Settings for opt-in hook enforcement: `approval_profiles`, `evidence_profiles`, `max_stop_blocks`, `edit_tools`, `shell_write_patterns`, and `irreversible_commands`. See [enforcement.md](enforcement.md).

### `[selection]`
`code_navigation_size_adjustment`: how strongly repository size (compared with `context.large_repo_files`) shifts code-navigation choices between built-in search and indexed tools such as code-graph MCP servers.

## Checking the effect of a change

```bash
susanoo doctor                 # validates configuration
susanoo eval                   # routing on the bundled suite
susanoo eval --suite my.toml   # your own labeled tasks
susanoo assess --json "..."    # inspect individual scores and reasons
```
