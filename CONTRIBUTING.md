# Contributing to Susanoo

Thanks for helping. Susanoo is young, so focused contributions with tests have the most impact.

## Development setup

```bash
git clone https://github.com/Breadstick-Crumbs/susanoo.git
cd susanoo
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Before opening a pull request, run what CI runs:

```bash
ruff check . && ruff format --check .
mypy
pytest -n auto                 # parallel; plain `pytest` runs serially
bandit -q -r src -c pyproject.toml
susanoo eval --min-agreement 0.9 --fail-on-risk-violation
```

## Ground rules

- **Behavior changes need tests.** Heuristic changes also need routing-suite cases (see below).
- **Keep runtime dependencies at zero** unless an ADR in `docs/decisions/` justifies one.
- **No unverifiable claims** in docs, output, or PR descriptions. If something is a heuristic, estimate, or plan, say so.
- **Discovery stays read-only.** New probes must not execute project code. Anything that executes must be opt-in.
- **Never read secrets into inventories, records, or output.** MCP `env`, `headers`, and `args` are off-limits.
- Treat repository content and tool output as untrusted input.

## Common contributions

### Add a tool to the registry

Edit `src/susanoo/data/tools.toml`:

1. Choose `kind` (`native`, `cli`, `script`, `skill`, `mcp`, `service`) and the `capabilities` it provides (`susanoo research --list`).
2. Add detection keys that work **without executing anything**: `binaries`, `python_packages`, `npm_packages`, `npm_scripts`, `make_targets`, `files`, `pyproject_tools`, `mcp_patterns`, `env`.
3. Add `run` commands only for safe, non-interactive, local checks. Skip `run` if the tool downloads rules, contacts services, or mutates state.
4. Score `reliability`, `context_cost`, `setup_cost`, and `security_exposure` from 1 to 3 and explain non-obvious scores in `notes`.
5. Add a test in `tests/test_discovery.py` or `tests/test_selection_research.py` showing detection and selection.

### Add or tune a signal

Edit `[[signals]]` in `src/susanoo/data/defaults.toml`, then:

1. Add cases to `src/susanoo/data/suites/routing-v1.toml`. **Write the label before you look at Susanoo's output.**
2. Run `susanoo eval`. Agreement must not drop, and risk-floor violations must stay at zero.
3. Add an analyzer test if the signal guards a safety property (for example, "X must never be low risk").

### Add or change an execution profile

Profiles are in `src/susanoo/data/profiles/`. Keep phases short and actionable, because Claude reads them. Mark `approval = true` only where a human decision adds value. Update `tests/test_router.py` and the docs.

### Add an evaluator or record field

Records live in `src/susanoo/evaluation/`. New fields must be optional so old records keep loading. New insights must respect `min_samples` and stay suggestions, never automatic changes.

### Add an integration for another AI coding environment

Create `src/susanoo/adapters/<environment>/` with discovery (read-only), an installer that follows the same safety rules as the Claude Code installer (manifest, no overwrites, hash-checked uninstall), and instruction templates. Add contract tests mirroring `tests/test_installer.py` and `tests/test_templates.py`.

### Change the Claude Code skill or subagents

Edit the templates in `src/susanoo/data/templates/`, then run `susanoo init` in the repository to update the dogfood copies in `.claude/`. `tests/test_templates.py` fails if they drift.

## Pull requests

- Keep each PR to one concern. Explain what changed and how you verified it, and paste the relevant command output.
- Update `CHANGELOG.md` under *Unreleased*.
- Make sure CI passes on all platforms.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
