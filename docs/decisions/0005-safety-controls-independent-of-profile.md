# 0005: Safety controls derive from risk, not from the chosen profile

**Status:** accepted (v0.1); approval checkpoints are reported, not enforced, while hands-off ([0007](0007-hands-off-by-default.md)).

## Context

Developers must be able to override the recommended workflow. They know things the heuristics do not, and forcing heavyweight process breeds workarounds. But an override must not quietly remove protections for security, data, or irreversible actions.

## Options

1. Overrides change everything, including checks.
2. Overrides are forbidden for high-risk tasks.
3. Overrides change the profile, while mandatory controls are computed from assessment dimensions and always apply. Critical risk has a configurable minimum profile.

## Decision

Option 3. Controls add approval checkpoints and force specific verification steps to *required* whatever profile is selected. When risk is critical, overrides below `routing.critical_risk_floor` (default STANDARD) are raised and the brief explains why.

## Consequences

- `susanoo route --profile fast` on a security-sensitive task gives a lighter process but keeps security review, its checkpoint, and security analysis.
- Projects can tighten the floor (for example to DEEP) or change control thresholds in configuration. That is a deliberate, reviewable change rather than a per-task override.
