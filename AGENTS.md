# Agent instructions for the DevRoute repository

Guidance for AI coding agents working on DevRoute itself.

## What this project is

DevRoute is a deterministic Python CLI and library (no runtime dependencies) that assesses software tasks, routes them to FAST, STANDARD, or DEEP workflows, discovers local capabilities, plans verification, and records evidence. Claude Code integration lives in `src/devroute/adapters/claude_code/` and `src/devroute/data/templates/`.

## Commands

```bash
pip install -e ".[dev]"
pytest                                   # full test suite
ruff check . && ruff format --check .    # lint + format
mypy                                     # strict type checking of src/devroute
bandit -q -r src -c pyproject.toml       # security lint
devroute eval                            # routing behavior on the reference suite
```

## Rules

- Python 3.11+, full type hints, `mypy --strict` clean. Standard library only at runtime (see `docs/decisions/0001`).
- Heuristics, profiles, the tool registry, and templates are data in `src/devroute/data/`. Prefer changing data over adding code paths.
- Any change to signals or thresholds needs `devroute eval` to keep 0 risk-floor violations and no agreement drop, plus new suite cases labeled before looking at the output.
- Discovery must stay read-only and must never read MCP `env`, `headers`, `args`, or URLs.
- After editing anything in `src/devroute/data/templates/`, run `devroute init` in the repo root so the `.claude/` dogfood copies stay in sync (`tests/test_templates.py` enforces this).
- Do not describe heuristic scores as validated, and do not claim unimplemented features exist. Update `docs/roadmap.md` when status changes.
- Record user-visible changes in `CHANGELOG.md` under *Unreleased*.

## Layout

See `docs/architecture/overview.md` for components and data flow.
