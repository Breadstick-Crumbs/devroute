# 0007: Hands-off by default

**Status:** accepted (v0.7). Supersedes [0005](0005-safety-controls-independent-of-profile.md) and [0006](0006-model-judgment-above-a-keyword-floor.md) where they conflict, as described below.

## Context

Claude Code already has a permission system: per-command allow and deny rules, and modes from asking about every edit to asking about nothing. Susanoo's gates added a second layer of questions on top. In Claude Code's normal mode that meant a plan approval for STANDARD and DEEP tasks and a confirmation for irreversible commands; v0.6 already turned these off in the autonomous modes (`autonomous_modes`). Users who run Claude Code hands-off found the extra questions redundant: they decide what needs confirmation in Claude Code, once, and want Susanoo to keep the work honest rather than ask again.

## Options

1. Keep the gates in Claude Code's normal and plan modes; step aside only in the autonomous modes (v0.6).
2. Hands-off by default: Susanoo asks nothing and blocks no edit in any mode; the evidence gate stays because it never asks the user anything; `hands_off = false` restores the gates.
3. Remove the asking gates entirely.

## Decision

Option 2, with `[enforcement] hands_off = true` as the default.

- **Off by default:**
  - the plan-approval gate;
  - the shell-write ask before approval;
  - the irreversible-action confirmation (force push, history rewrite, deploy and publish, `terraform destroy`, `kubectl delete`, destructive SQL), in every permission mode including normal and plan;
  - the route-integrity asks.

  Claude Code's own permission settings decide what needs confirmation. A command the user allowlisted in Claude Code therefore runs without any confirmation.
- **Kept in every mode:**
  - the evidence gate;
  - the refusal of `--accept-missing-evidence`;
  - the `ask` on `susanoo approve`, so Claude can never record an approval the user did not give, which would otherwise count later if the user sets `hands_off = false`;
  - agent-facing nudges that never ask the user anything, such as route-before-edit.
- **Briefs and instructions follow the setting.** Checkpoints are reported rather than waited at, and the irreversible-action control describes what to avoid rather than promising a confirmation that will not come.
- **Supersedes 0005 where they conflict.** Mandatory controls still derive from risk and still survive a profile override (security review, data safety, sandbox credentials, required verification), but their approval checkpoints are reported rather than enforced while hands-off.
- **Supersedes 0006 where they conflict.** After `susanoo raise`, the plan is approved again only with `hands_off = false`; hands-off carries on without a new approval.
- **Configuration only.** `hands_off` comes from configuration files (packaged defaults, `~/.susanoo/config.toml`, the project's `.susanoo/config.toml`), never from a command-line flag or a message. In automatic-routing sessions `route` and `raise` refuse `--config`.

## Consequences

- Existing `--enforce` users get the change on upgrade: irreversible commands no longer ask in normal mode. The CHANGELOG lists it as a breaking change, and `hands_off = false` restores the previous behavior.
- Fewer interruptions, and one place (Claude Code) to decide what needs confirmation.
- As before, the gates are guardrails against drift, not a security boundary: the project's configuration file is editable like any other file.
