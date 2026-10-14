# Agent instructions for the Susanoo repository

Guidance for AI coding agents working on Susanoo itself.

## What this project is

Susanoo is a deterministic Python CLI and library (no runtime dependencies) that assesses software tasks, routes them to FAST, STANDARD, or DEEP workflows, discovers local capabilities, plans verification, and records evidence. Claude Code integration lives in `src/susanoo/adapters/claude_code/` and `src/susanoo/data/templates/`.

## Commands

```bash
pip install -e ".[dev]"
pytest -n auto                           # full test suite, in parallel (pytest-xdist)
ruff check . && ruff format --check .    # lint + format
mypy                                     # strict type checking of src/susanoo
bandit -q -r src -c pyproject.toml       # security lint
susanoo eval                             # routing behavior on the reference suite
```

## Testing while you work

The full suite takes minutes, and several agents often work on branches at once. Run it when
the code has changed in a way nobody has tested, not on every edit:

- While iterating, run only the affected tests: `pytest tests/test_x.py` or `pytest -k <name>`.
- Run the full suite (`pytest -n auto`) once after each merge of `main` into your branch (the
  combination is new) and once before pushing. Do not rerun it when only docs or comments changed
  since the last full run.
- Rerun `susanoo eval` only after changing signals, thresholds, profiles, or the routing suite.
- CI runs everything on three operating systems; do not repeat locally what it will run anyway.

## Rules

- Python 3.11+, full type hints, `mypy --strict` clean. Standard library only at runtime (see `docs/decisions/0001`).
- Heuristics, profiles, the tool registry, and templates are data in `src/susanoo/data/`. Prefer changing data over adding code paths.
- Any change to signals or thresholds needs `susanoo eval` to keep 0 risk-floor violations and no agreement drop, plus new suite cases labeled before looking at the output.
- Discovery must stay read-only and must never read MCP `env`, `headers`, `args`, or URLs.
- After editing anything in `src/susanoo/data/templates/`, run `susanoo init` in the repo root so the `.claude/` dogfood copies stay in sync (`tests/test_templates.py` enforces this).
- Do not describe heuristic scores as validated, and do not claim unimplemented features exist. Update `docs/roadmap.md` when status changes.
- Record user-visible changes in `CHANGELOG.md` under *Unreleased*. A release bumps the version in `pyproject.toml`, `src/susanoo/__init__.py`, and `CITATION.cff`.
- `docs/assets/demo.svg` is generated from real routing output: rerun `python scripts/make_demo_svg.py` when routing changes (a test checks it). Brand assets (logo, banners, social card) come from `python scripts/make_brand_assets.py`; it needs Edge or Chrome to render PNGs.

## Layout

See `docs/architecture/overview.md` for components and data flow.
