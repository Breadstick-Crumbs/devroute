## What and why

<!-- What does this change, and what problem does it solve? Link the issue. -->

## How it was verified

<!-- Paste the commands you ran and their results. Say explicitly what you did not verify. -->

- [ ] `pytest`
- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy`
- [ ] `devroute eval` (required if signals, thresholds, profiles, or controls changed: no agreement drop, 0 risk-floor violations)

## Checklist

- [ ] Tests added or updated for behavior changes
- [ ] Docs and `CHANGELOG.md` (*Unreleased*) updated
- [ ] No new runtime dependencies (or an ADR explains why)
- [ ] Discovery remains read-only; no secrets are read into output or records
- [ ] If templates changed: `devroute init` was run so `.claude/` copies are in sync
