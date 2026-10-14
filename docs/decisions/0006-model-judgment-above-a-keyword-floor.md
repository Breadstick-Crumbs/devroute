# 0006: Claude's judgment above a keyword floor

**Status:** accepted (v0.7). Extends [0002](0002-deterministic-heuristic-routing.md) for automatic routing. After a raise, the plan is approved again only with `hands_off = false` ([0007](0007-hands-off-by-default.md)).

## Context

Since v0.6, `susanoo init --global` routes every prompt. Two jobs use keywords:

1. **Is this a coding task?** Only prompts that start with a task verb are routed. "The checkout page is broken" or "users can't log in since yesterday's deploy" are not routed at all, although they are often exactly the tasks that need care.
2. **How risky is it?** Keywords catch most risk ("payments", "drop table"), but they miss phrasings they were not written for: "remove all inactive customers from the database" is a bulk delete, and it was rated moderate risk.

An LLM is good at both jobs, but a separate model call from the hook needs an API key, or a headless `claude -p` that takes seconds and uses quota on every prompt. A model's answer also varies between runs, and a model that sets its own route could choose a light one and skip its own guardrails.

## Options

1. Keep keywords only.
2. Call a model from the hook to classify every prompt.
3. Let the Claude that is already running decide where keywords are unsure, with the keyword result as a minimum it cannot go below.

## Decision

Option 3:

- **Keywords set a floor from the user's own words.** The `UserPromptSubmit` hook assesses every message and saves its keyword floor for the session: dimension levels, signal ids, areas, and the profile the message alone gets. It never saves the message text. The floor reads the first 20,000 characters of a message, so a pasted log cannot slow every prompt down. A vague message ("ok", "what does this do?") does not count as risky in the floor; the route built on it gets its own confidence from Claude's description. This costs no model call and no noticeable time.
- **Claude decides whether an unrouted message is a coding task.** A message that was not routed gets a one-line note. If handling it means changing code, Claude runs `susanoo route "<task>"` itself. In a session with automatic routing, that route combines Claude's description with the floor: every dimension takes the stricter level, the message's signals and areas still count, and the profile is at least the message's own.
- **A replaced route is a floor too.** If a route is open when a new one is made (by Claude or by `/susanoo`, after any message), the open route's own levels, signals, and profile join the floor, so a FAST "tidy the docstrings" route cannot quietly replace a DEEP "drop the table" route. To start fresh, record the open route's outcome first. A route with unfinished work is never replaced.
- **Claude can raise a route but not lower it.** `susanoo raise standard|deep --reason "<the risk>"` makes the open route stricter. The open route is the floor, and the risk named in the reason is assessed and added, so "deletes production data" turns on the data-safety and irreversible-action controls. Evidence already recorded carries over. A raise always changes the plan (a stricter profile adds phases, a new control adds a check), so the user's plan approval does not carry over and the plan is approved again. Autonomy the user asked for in words always carries over. The target profile is a minimum: a reason that routes higher wins. Raising to the same profile only re-plans when the reason adds a safety control, so a DEEP route can still gain one.
- **`--profile` changes only the profile.** With automatic routing, the floor applies to every route, with or without `--profile`, so an override never removes a safety control, even `--profile deep`. A profile below Susanoo's recommendation for the task (the message, the open route, and Claude's description together) is refused by `susanoo route` itself unless the user's latest message named that profile (`/susanoo --profile fast ...`, "use the fast workflow"); Claude asks the user instead. The check runs in Susanoo, not on the shell text, so quoting or shell variables do not get around it; like the other gates it guards against drift, not a hostile agent (one that changes Susanoo's environment, such as `SUSANOO_HOME`, can still avoid it). In such a session `route` and `raise` refuse `--config` (an extra file could switch controls off), a route never drops a control the open route has, and a missing or unreadable prompt record still leaves the open route as a floor. `/susanoo` keeps its full brief and normal evidence rules, with the same floors.

## Consequences

- Prompts phrased as problems are routed when Claude judges they need code changes, and Claude can catch risk the keywords missed.
- The guarantee is deterministic and tested: for every pair of reference-suite tasks, a combined route is never below the user's message on profile, dimensions, risk, safety controls, or signals, and no `--profile` override removes a control. Raising and replacing routes have targeted tests, including tasks longer than the stored task text and every kind of message (task, question, `/command`, `!command`, `?`).
- The floor ignores anything after the first 20,000 characters of a message.
- The hook's own task routing is unchanged: a new task prompt that routes higher than the open one still replaces it (v0.6 behavior). "Never replaces unfinished work" applies to routes made with `susanoo route`.
- Whether Claude routes an unrouted message, and how it describes the task, depends on the model and varies between runs. A live check showed Claude skipping the note once in five runs, so a backstop was added: the first edit of a project file for an unrouted message is denied once with an instruction to route first (`[auto] route_before_edit`). It applies in every permission mode, because it only makes Claude do Susanoo's own bookkeeping and never asks the user. A retry goes through, and shell-command edits are not covered. Claude can raise a route, but nothing forces it to notice risk the keywords missed.
- Gates are still guardrails against drift, not a security boundary: an agent determined to avoid them could, for example, not route a task at all. The floor protects against paraphrase, not against a hostile agent.
- `susanoo eval` still measures the keyword router only. The model's part needs live checks or outcome records.
