# 0009: Hands-off covers permission prompts, not clarifying questions

**Status:** accepted (v0.8). Clarifies [0007](0007-hands-off-by-default.md); does not change its gates.

## Context

ADR 0007 made Susanoo hands-off by default: it adds no approvals or confirmations, and Claude Code's permission settings decide what needs one. What the user wanted gone were permission prompts: being asked about every deletion, edit, or push, which Claude Code already asks about where it matters.

The automatic-routing brief went further than that. It told Claude to "work autonomously" and never mentioned clarifying questions, even when Susanoo's own assessment had low confidence. The low-confidence "confirm the task scope" checkpoint was only reported, never shown in that brief. The playbook rule "if requirements are unclear, ask instead of guessing, unless I asked you to work autonomously" could then read as switched off by Susanoo's own wording, although the user never asked for that. A guess about what the user meant can produce confident, wrong work.

## Options

1. Keep the brief as it is: Claude asks only when it decides to on its own.
2. Tell Claude to ask questions on every routed task.
3. Ask only when there is a reason: the assessment has low confidence, or an ambiguity would change what gets built. Never when the user asked in words not to be asked.

## Decision

Option 3. Hands-off means no permission or approval prompts from Susanoo. It does not mean "never ask about the task".

- The brief says "Do not wait for approval" instead of "Work autonomously", so it no longer sounds like the user asked for autonomy.
- It adds: "If the request is ambiguous in a way that changes what you build, ask the user instead of guessing." With `hands_off = false`, the plan check-in asks Claude to include such questions with the plan.
- When the assessment of the route has low confidence (a very short description, or no recognizable signals), the brief lists why and tells Claude to ask one to three focused questions about scope and expected behavior before editing, unless the user's message or the conversation already answers them. When Claude routes a task itself, this is its own description, so a terse description can ask about a detailed message; "unless ... already answers them" covers that.
- When the user asked for autonomy in words ("work autonomously", "don't ask", "no questions"), neither line appears: in the brief for that message, after a mid-task request (the follow-up note also says not to stop for questions), when Claude routes the task anew, and after `susanoo raise`.
- No gate changes. Nothing waits for an answer. The evidence gate does not block ending a turn to ask before any file has changed; after edits, a turn ends only once required checks have evidence, or after `max_stop_blocks` blocks, as before.

## Consequences

- Vague tasks get a question before any edit, which costs one exchange and avoids rework. A question asked after edits meets the evidence gate like any other turn end.
- Whether Claude asks still depends on the model; the brief makes it explicit, it does not enforce it.
- `/susanoo` already told Claude to ask clarifying questions when confidence is low; the automatic brief now matches it.
