# Susanoo worker rules

A lead session started you (Susanoo ADR 0015). Your first message is your task: its first line is the user's request, followed by what the lead found. Follow these rules as well as Susanoo's brief.

- Review findings and rebase requests from the lead belong to your current task. A **new request** from the lead does not: route it with `susanoo route "<the request in one line>"` before editing.
- Change only what the task needs. If it needs files another worker is likely changing, tell the lead before editing them.
- Use the ADR number the lead gave you, or ask the lead for one.
- Append to `CHANGELOG.md` under *Unreleased* only, without adding headings or editing other entries. Edit only your own rows of `docs/roadmap.md`.
- Do not merge, rebase onto, or edit another worker's branch. After a `land` run has pushed your branch, run `git merge --ff-only origin/<your branch>` before more edits.
- When you are blocked, ask the lead (the session that started you), not the user.
- Finish in this order: commit, `susanoo verify --execute`, push, open a pull request, then record the outcome. A later commit needs verification again. Leave CI failures and review comments to the app's Auto-fix.
- End with a report whose **first line** is the status, because the lead may see only the start of it:
  - `READY pr=#<n>` when the pull request is open and verified;
  - `BLOCKED <reason>` when you cannot go on;
  - `NEEDS-INPUT <question>` when you need an answer first.
