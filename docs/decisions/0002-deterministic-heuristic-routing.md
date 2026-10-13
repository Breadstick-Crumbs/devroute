# 0002: Deterministic, configurable heuristics for assessment and routing

> Written when the project was named DevRoute; names were updated when it was renamed to Susanoo (v0.3).

**Status:** accepted (v0.1). Extended by [0006](0006-model-judgment-above-a-keyword-floor.md): with automatic routing, Claude's judgment applies above the keyword result.

## Context

Routing could be done by asking a model to classify the task, by a trained classifier, or by explicit heuristics. The router decides how much process and verification a task gets, so its behavior must be predictable, explainable, testable, and cheap.

## Options

1. Model-based classification at routing time.
2. A trained classifier.
3. Explicit signal and threshold heuristics in TOML.

## Decision

Option 3. Every score carries its reasons. The same input always gives the same output, so routing can be regression-tested (`susanoo eval`) and changed through reviewed configuration. No labeled outcome data exists yet, which rules out option 2. Option 1 adds cost and latency and makes behavior hard to reproduce.

## Consequences

- Heuristics are keyword-based and miss context not present in the text (negation, implied scope). This is documented as a limitation.
- Thresholds are hand-tuned, and Susanoo does not claim they are validated.
- Inside Claude Code the model still reads the brief and asks clarifying questions when confidence is low, so model judgment is applied, just not in the routing step itself.
- Once outcome records accumulate, a learned or model-assisted router can be evaluated against this baseline with the same suite and records (roadmap phase 5).
