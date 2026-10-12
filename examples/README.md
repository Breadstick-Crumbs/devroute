# Examples

Three execution briefs for tasks of increasing complexity and risk, all run against [`sample-app/`](sample-app/), a tiny Python stock-tracking package with pytest tests.

| Example | Task | Routed to | Why |
|---|---|---|---|
| [01-simple.md](01-simple.md) | Fix the typo in the `OutOfStockError` message | **FAST** | complexity, risk, and uncertainty are all low |
| [02-moderate.md](02-moderate.md) | Add a low-stock alert that emails the warehouse | **STANDARD** | moderate complexity; email delivery adds an external dependency |
| [03-complex.md](03-complex.md) | Build a multi-warehouse inventory platform with auth, Stripe billing, and an admin dashboard | **DEEP** | critical risk (security, data integrity, external dependencies) and high complexity |

## Reproduce

```bash
pip install -e .          # from the repository root
devroute route --path examples/sample-app "Fix the typo in the OutOfStockError message"
python examples/generate.py   # regenerate all three files
```

The **assessment, routing, controls, steps, and checkpoints** are deterministic and match on any machine with the same DevRoute version. The **Capabilities** and **Required verification** sections depend on which tools are installed where you run it. The committed copies were generated inside a Claude Code session, with pytest, ruff, mypy, and bandit available and no other user-level Claude Code configuration.

## Try the evidence flow

`sample-app` sits inside this repository, so pass `--path` to keep DevRoute from using the repository root (the nearest directory with `.git`) as the project:

```bash
APP=examples/sample-app
devroute init --path $APP
devroute route --path $APP "Fix the typo in the OutOfStockError message"
devroute verify --path $APP --execute      # runs pytest and ruff, stores evidence
devroute outcome --path $APP latest --status completed --minutes 3
devroute report --path $APP
devroute init --path $APP --uninstall      # remove the integration again (records are kept)
```
