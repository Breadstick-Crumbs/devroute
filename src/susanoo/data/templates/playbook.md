<!-- susanoo:begin (managed by `susanoo init --global`; edits inside this block are replaced) -->
## Workflow: Susanoo

Coding tasks go through Susanoo. When I give you a task, a brief that starts with "Susanoo routed
this task" is added to your context automatically. Follow it:

1. Tell me the workflow in one or two lines: FAST, STANDARD, or DEEP, and why.
2. FAST: make the change, run the focused checks, report.
3. STANDARD and DEEP: investigate read-only, then present a short plan and wait for my approval
   (one check-in). Approval is recorded with `{command} approve`, which I confirm.
4. Autonomous: if Claude Code runs in an autonomous permission mode (accept edits, auto, bypass)
   or I ask you to work autonomously, do not wait for approval and do not ask questions you can
   answer yourself. Claude Code's own permission settings decide what needs confirmation.
5. DEEP: work on a branch or worktree, and get an independent review from the susanoo-reviewer
   agent before finishing.
6. Before finishing: run `{command} verify --execute` and fix failures. Never claim success
   without passing evidence, and list anything that was not verified.
7. Record the result: `{command} outcome latest --status completed|partial|abandoned --source agent`.

Questions and explanations need no brief: just answer. A message that starts with "?" is never
routed. Follow this workflow only when a Susanoo brief is in your context: without one, Susanoo is
off for this folder or the message was not a clear task, so work as you normally would.
<!-- susanoo:end -->
