# 0001: Python standard library only at runtime

**Status:** accepted (v0.1)

## Context

DevRoute runs inside developers' environments, often alongside the project's own virtualenv, and is invoked by an AI agent through a shell. Every dependency adds install friction, version conflicts, and supply-chain exposure.

## Options

1. Standard library only (`argparse`, `tomllib`, `json`, `dataclasses`).
2. Rich CLI stack (Click/Typer + Rich + Pydantic).
3. YAML configuration (PyYAML).

## Decision

Option 1. Python 3.11+ provides `tomllib`, so configuration can be TOML without a dependency. Output is plain Markdown, which reads well in terminals and inside Claude Code. Claude Code frontmatter is parsed with a small, tested parser for the subset those files use.

## Consequences

- `pip install devroute` pulls in nothing else. Development tools (pytest, ruff, mypy, bandit) are optional extras.
- Terminal output is less decorated.
- If configuration validation grows much more complex, adding a schema library is a reasonable future decision.
