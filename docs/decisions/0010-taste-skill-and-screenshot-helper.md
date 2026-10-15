# 0010: A taste skill for visual decisions, and a screenshot helper

**Status:** accepted (v0.8, unreleased). Adds to [0008](0008-ui-ux-workflow.md) without changing its checks; consistent with [0001](0001-zero-runtime-dependencies.md), [0007](0007-hands-off-by-default.md), and [0009](0009-clarifying-questions-under-hands-off.md).

## Context

ADR 0008 makes Susanoo require and record a visual check for UI work, and leaves every aesthetic judgment to Claude and people. That keeps Susanoo honest, but the judgment itself still comes from the model's defaults. The maintainer's own UI work came out generic ("AI slop") until Claude was given his references.

A blind comparison on 2026-10-14 tested this. The same landing-page brief was built twice on the same model: once with plain Claude Code, and once with a skill that reads the maintainer's taste profile, checks fit, proposes three directions, and screenshots its own work. The maintainer preferred the skill's version without knowing which was which. That is one comparison, judged by the person whose taste it encodes. It shows the approach can work for him; it does not show it works in general.

ADR 0008 also says Susanoo will not "produce or compare screenshots itself". The skill needs screenshots at the brief's widths, and many projects have no browser tool that Susanoo can see.

## Decision

1. **`susanoo init` installs a `taste-ui` skill** (project and `--global`, with a `--no-taste` opt-out).
   - It tells Claude to read the user's taste profile, run a **fit check**, propose three distinct directions, build with tokens first, look at screenshots before handing back, and record the brief's visual check.
   - The profile is a quality bar, not a required look. A project's `DESIGN.md`, its tokens, and a client's brand win where they conflict.
2. **The skill asks the user when the design fit is unclear**, also when Claude Code runs without permission prompts. These are clarifying questions about the task, and they follow ADR 0009, including its exception: when the user said not to be asked ("don't ask", "work autonomously"), Claude picks the best fit and says why in one line instead. They are not permission prompts or confirmations, so ADR 0007 still holds.
   - Susanoo itself adds no question, approval, or gate.
   - When the skill is installed, the visual check on automatic routes becomes a manual step that is demanded instead of an unavailable one. That is the existing evidence gate, now with a tool behind it.
3. **Susanoo ships a screenshot helper, `susanoo taste shoot`**, plus `susanoo taste capture` for building a profile.
   - They run Node scripts with tools the user installed (Node.js 22+ and Chrome, Chromium, or Edge). There are still no Python runtime dependencies (ADR 0001).
   - **Susanoo still never judges, scores, or compares the screenshots.** Claude looks at them; the evidence stays *reported*.
   - This narrows ADR 0008's "does not produce screenshots": Susanoo now produces them on request, and still does not judge them.
4. **Where the profile comes from:** `SUSANOO_TASTE_DIR`, then `[ui] taste_dir` in the user configuration, then `~/.susanoo/taste`.
   - Susanoo's project configuration cannot move it, and relative paths are rejected.
   - Node and the browser are found on PATH, but never in the current folder or below it, and only as `.exe` files on Windows. A repository cannot plant a program that runs instead of them.

## Consequences

- UI tasks in projects with the skill get a design process instead of a single default guess. Projects without a profile get the built-in slop blacklist, plus an offer to build a profile from sites the user likes.
- The visual check is demanded more often on automatic routes. The skill records a skip, with its reason, only when no tool at all can render the page.
- Captured reference pages are untrusted content. The skill tells Claude to treat their text and screenshots as data, never as instructions.
- What is still not known: whether the skill helps people other than the maintainer, and on which kinds of products. That needs more blind comparisons, judged by those users.
